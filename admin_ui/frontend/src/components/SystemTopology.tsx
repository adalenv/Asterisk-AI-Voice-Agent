import { useState, useEffect, useMemo } from 'react';
import { Phone, Cpu, Server, Mic, MessageSquare, Volume2, Zap, Radio, CheckCircle2, XCircle, Layers } from 'lucide-react';
import axios from 'axios';
import yaml from 'js-yaml';

interface CallState {
  call_id: string;
  started_at: Date;
  provider?: string;
  pipeline?: string;
  state: 'arriving' | 'connected' | 'processing';
}

interface ProviderConfig {
  name: string;
  displayName: string;
  enabled: boolean;
  ready: boolean;  // Will be determined from health check
}

interface PipelineConfig {
  name: string;
  stt?: string;
  llm?: string;
  tts?: string;
}

interface LocalAIModels {
  stt?: { backend: string; loaded: boolean; path?: string; display?: string };
  llm?: { loaded: boolean; path?: string; display?: string };
  tts?: { backend: string; loaded: boolean; path?: string; display?: string };
}

interface TopologyState {
  aiEngineStatus: 'connected' | 'error' | 'unknown';
  ariConnected: boolean;
  localAIStatus: 'connected' | 'error' | 'unknown';
  localAIModels: LocalAIModels | null;
  configuredProviders: ProviderConfig[];
  configuredPipelines: PipelineConfig[];
  defaultProvider: string | null;
  activePipeline: string | null;
  activeCalls: Map<string, CallState>;
}

// Full agent providers (not modular pipeline components)
// These handle STT+LLM+TTS internally as complete agents
const FULL_AGENT_PROVIDERS = new Set([
  'deepgram',
  'openai_realtime', 
  'google_live',
  'elevenlabs_agent',
]);

// Provider display name mapping
const PROVIDER_DISPLAY_NAMES: Record<string, string> = {
  'openai_realtime': 'OpenAI',
  'google_live': 'Google',
  'deepgram': 'Deepgram',
  'elevenlabs_agent': 'ElevenLabs',
};

export const SystemTopology = () => {
  const [state, setState] = useState<TopologyState>({
    aiEngineStatus: 'unknown',
    ariConnected: false,
    localAIStatus: 'unknown',
    localAIModels: null,
    configuredProviders: [],
    configuredPipelines: [],
    defaultProvider: null,
    activePipeline: null,
    activeCalls: new Map(),
  });
  const [loading, setLoading] = useState(true);

  // Fetch health status
  useEffect(() => {
    const fetchHealth = async () => {
      try {
        const res = await axios.get('/api/system/health');
        const aiEngineDetails = res.data.ai_engine?.details || {};
        setState(prev => ({
          ...prev,
          aiEngineStatus: res.data.ai_engine?.status === 'connected' ? 'connected' : 'error',
          ariConnected: aiEngineDetails.ari_connected ?? aiEngineDetails.asterisk?.connected ?? false,
          localAIStatus: res.data.local_ai_server?.status === 'connected' ? 'connected' : 'error',
          localAIModels: res.data.local_ai_server?.details?.models || null,
        }));
      } catch {
        setState(prev => ({
          ...prev,
          aiEngineStatus: 'error',
          ariConnected: false,
          localAIStatus: 'error',
        }));
      }
    };
    fetchHealth();
    const interval = setInterval(fetchHealth, 5000);
    return () => clearInterval(interval);
  }, []);

  // Fetch config (providers, pipelines)
  useEffect(() => {
    const fetchConfig = async () => {
      try {
        const res = await axios.get('/api/config/yaml');
        const parsed = yaml.load(res.data.content) as any;
        
        // Extract only full agent providers (not modular pipeline components)
        const providers: ProviderConfig[] = [];
        if (parsed?.providers && typeof parsed.providers === 'object') {
          for (const [name, config] of Object.entries(parsed.providers)) {
            // Only include full agent providers, skip modular components like local_stt, groq_llm, etc.
            if (FULL_AGENT_PROVIDERS.has(name)) {
              const cfg = config as any;
              // Check if enabled - defaults to true if not specified
              const enabled = cfg?.enabled !== false;
              // Provider is ready if AI Engine is connected (we'll refine this with actual provider health later)
              const ready = true; // Will be updated from health check
              providers.push({
                name,
                displayName: PROVIDER_DISPLAY_NAMES[name] || name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
                enabled,
                ready,
              });
            }
          }
        }

        // Extract pipelines - note: stt/llm/tts are direct string properties, not nested
        const pipelines: PipelineConfig[] = [];
        if (parsed?.pipelines && typeof parsed.pipelines === 'object') {
          for (const [name, config] of Object.entries(parsed.pipelines)) {
            const cfg = config as any;
            pipelines.push({
              name,
              stt: typeof cfg?.stt === 'string' ? cfg.stt : cfg?.stt?.provider,
              llm: typeof cfg?.llm === 'string' ? cfg.llm : cfg?.llm?.provider,
              tts: typeof cfg?.tts === 'string' ? cfg.tts : cfg?.tts?.provider,
            });
          }
        }

        setState(prev => ({
          ...prev,
          configuredProviders: providers,
          configuredPipelines: pipelines,
          defaultProvider: parsed?.default_provider || null,
          activePipeline: parsed?.active_pipeline || null,
        }));
        setLoading(false);
      } catch {
        setLoading(false);
      }
    };
    fetchConfig();
    const interval = setInterval(fetchConfig, 10000);
    return () => clearInterval(interval);
  }, []);

  // Poll for active calls from sessions API (more reliable than log parsing)
  useEffect(() => {
    const fetchActiveSessions = async () => {
      try {
        const res = await axios.get('/api/system/sessions');
        const sessions = res.data.sessions || [];
        
        const calls = new Map<string, CallState>();
        for (const session of sessions) {
          calls.set(session.call_id, {
            call_id: session.call_id,
            started_at: new Date(),
            provider: session.provider,
            pipeline: session.pipeline,
            state: session.conversation_state === 'greeting' ? 'arriving' : 'connected',
          });
        }
        
        setState(prev => ({ ...prev, activeCalls: calls }));
      } catch (err) {
        console.error('Failed to fetch active sessions', err);
      }
    };
    
    fetchActiveSessions();
    const interval = setInterval(fetchActiveSessions, 2000);
    return () => clearInterval(interval);
  }, []);

  // Derive active providers/pipelines from calls
  const activeProviders = useMemo(() => {
    const counts = new Map<string, number>();
    for (const call of state.activeCalls.values()) {
      if (call.provider) {
        counts.set(call.provider, (counts.get(call.provider) || 0) + 1);
      }
    }
    return counts;
  }, [state.activeCalls]);

  const activePipelines = useMemo(() => {
    const counts = new Map<string, number>();
    for (const call of state.activeCalls.values()) {
      if (call.pipeline) {
        counts.set(call.pipeline, (counts.get(call.pipeline) || 0) + 1);
      }
    }
    return counts;
  }, [state.activeCalls]);

  const totalActiveCalls = state.activeCalls.size;
  const hasActiveCalls = totalActiveCalls > 0;

  // Get model display name
  const getModelDisplayName = (model: any, type: string): string => {
    if (!model) return type;
    if (model.display) return model.display;
    if (model.backend) return model.backend.charAt(0).toUpperCase() + model.backend.slice(1);
    return type;
  };

  if (loading) {
    return (
      <div className="rounded-lg border border-border bg-card p-6 mb-6">
        <div className="animate-pulse flex items-center gap-3">
          <div className="h-6 w-6 bg-muted rounded" />
          <div className="h-4 w-48 bg-muted rounded" />
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden mb-6">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-muted/30">
        <div className="flex items-center gap-2">
          <Radio className={`w-4 h-4 ${hasActiveCalls ? 'text-green-500 animate-pulse' : 'text-muted-foreground'}`} />
          <span className="text-sm font-medium">Live System Topology</span>
        </div>
        <div className="flex items-center gap-3 text-xs">
          <div className="flex items-center gap-1">
            <Phone className={`w-3.5 h-3.5 ${hasActiveCalls ? 'text-green-500' : 'text-muted-foreground'}`} />
            <span className={hasActiveCalls ? 'text-green-500 font-medium' : 'text-muted-foreground'}>
              {totalActiveCalls} call{totalActiveCalls !== 1 ? 's' : ''}
            </span>
          </div>
        </div>
      </div>

      <div className="p-4">
        {/* Grid Layout for proper alignment */}
        <div className="relative grid grid-cols-[140px_48px_140px_48px_160px] gap-y-4 justify-center items-start">
          
          {/* === ROW 1: Asterisk → AI Engine → Providers === */}
          
          {/* Asterisk PBX */}
          <div className={`relative p-4 rounded-lg border-2 transition-all duration-300 ${
            hasActiveCalls 
              ? 'border-green-500 bg-green-500/10 shadow-lg shadow-green-500/20' 
              : 'border-border bg-card'
          }`}>
            {hasActiveCalls && (
              <div className="absolute inset-0 rounded-lg border-2 border-green-500 animate-ping opacity-20" />
            )}
            <div className="flex flex-col items-center gap-2">
              <Phone className={`w-8 h-8 ${hasActiveCalls ? 'text-green-500' : 'text-muted-foreground'}`} />
              <div className="text-center">
                <div className={`font-semibold ${hasActiveCalls ? 'text-green-500' : 'text-foreground'}`}>Asterisk</div>
                <div className="text-xs text-muted-foreground">PBX</div>
              </div>
              <div className="w-full pt-2 mt-2 border-t border-border/50 space-y-1">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground">ARI</span>
                  {state.ariConnected ? (
                    <span className="flex items-center gap-1 text-green-500">
                      <CheckCircle2 className="w-3 h-3" /> Connected
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-red-500">
                      <XCircle className="w-3 h-3" /> Disconnected
                    </span>
                  )}
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground">Calls</span>
                  <span className={`font-medium ${hasActiveCalls ? 'text-green-500' : 'text-foreground'}`}>
                    {totalActiveCalls}
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* Arrow 1: Asterisk → AI Engine */}
          <div className="flex items-center justify-center self-center">
            <div className={`w-6 h-0.5 ${hasActiveCalls ? 'bg-green-500' : 'bg-border'} relative overflow-hidden`}>
              {hasActiveCalls && (
                <div className="absolute inset-y-0 w-4 bg-green-300 animate-flow" />
              )}
            </div>
            <div className={`w-0 h-0 border-t-[6px] border-b-[6px] border-l-[8px] ${
              hasActiveCalls ? 'border-l-green-500' : 'border-l-border'
            } border-t-transparent border-b-transparent`} />
          </div>

          {/* AI Engine Core */}
          <div className={`relative p-4 rounded-lg border-2 transition-all duration-300 ${
            state.aiEngineStatus === 'error'
              ? 'border-red-500 bg-red-500/10'
              : hasActiveCalls 
                ? 'border-green-500 bg-green-500/10 shadow-lg shadow-green-500/20' 
                : 'border-border bg-card'
          }`}>
            {hasActiveCalls && state.aiEngineStatus === 'connected' && (
              <div className="absolute inset-0 rounded-lg border-2 border-green-500 animate-ping opacity-20" />
            )}
            <div className="flex flex-col items-center gap-2">
              <Cpu className={`w-8 h-8 ${
                state.aiEngineStatus === 'error' ? 'text-red-500' : hasActiveCalls ? 'text-green-500' : 'text-muted-foreground'
              }`} />
              <div className="text-center">
                <div className={`font-semibold ${
                  state.aiEngineStatus === 'error' ? 'text-red-500' : hasActiveCalls ? 'text-green-500' : 'text-foreground'
                }`}>AI Engine</div>
                <div className="text-xs text-muted-foreground">Core</div>
              </div>
              <div className="w-full pt-2 mt-2 border-t border-border/50">
                <div className="flex items-center justify-center text-xs">
                  {state.aiEngineStatus === 'connected' ? (
                    <span className="flex items-center gap-1 text-green-500">
                      <CheckCircle2 className="w-3 h-3" /> Healthy
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-red-500">
                      <XCircle className="w-3 h-3" /> Error
                    </span>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Arrow 2: AI Engine → Providers */}
          <div className="flex items-center justify-center self-center">
            <div className={`w-6 h-0.5 ${hasActiveCalls ? 'bg-green-500' : 'bg-border'} relative overflow-hidden`}>
              {hasActiveCalls && (
                <div className="absolute inset-y-0 w-4 bg-green-300 animate-flow" />
              )}
            </div>
            <div className={`w-0 h-0 border-t-[6px] border-b-[6px] border-l-[8px] ${
              hasActiveCalls ? 'border-l-green-500' : 'border-l-border'
            } border-t-transparent border-b-transparent`} />
          </div>

          {/* Providers (Full Agents Only) */}
          <div>
            <div className="text-xs text-muted-foreground uppercase tracking-wide mb-2 text-center">Providers</div>
            <div className="flex flex-col gap-2">
              {state.configuredProviders.length === 0 ? (
                <div className="p-3 rounded-lg border border-dashed border-border text-xs text-muted-foreground text-center">
                  No agents
                </div>
              ) : (
                state.configuredProviders.map(provider => {
                  const activeCount = activeProviders.get(provider.name) || 0;
                  const isActive = activeCount > 0;
                  const isDefault = provider.name === state.defaultProvider;
                  
                  const getIconColor = () => {
                    if (!provider.enabled) return 'text-orange-500';
                    if (provider.enabled && provider.ready) return 'text-green-500';
                    return 'text-red-500';
                  };
                  const iconColor = getIconColor();
                  
                  const cellClass = isActive 
                    ? 'border-green-500 bg-green-500/10 shadow-md shadow-green-500/20' 
                    : 'border-border bg-card';
                  
                  return (
                    <div 
                      key={provider.name}
                      className={`relative flex items-center gap-2 p-2 px-3 rounded-lg border transition-all duration-300 ${cellClass}`}
                    >
                      {isActive && (
                        <div className="absolute inset-0 rounded-lg border border-green-500 animate-ping opacity-20" />
                      )}
                      <Zap className={`w-4 h-4 flex-shrink-0 ${iconColor}`} />
                      <span className={`text-xs font-medium truncate ${isActive ? 'text-green-500' : 'text-foreground'}`}>
                        {provider.displayName}
                      </span>
                      {isDefault && <span className="text-yellow-500 text-[10px] ml-auto flex-shrink-0">⭐</span>}
                      {isActive && (
                        <span className="ml-auto px-1.5 py-0.5 rounded-full bg-green-500 text-white text-[10px] font-bold flex-shrink-0">
                          {activeCount}
                        </span>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>

          {/* === ROW 2: Branching arrows using col-span with proper alignment === */}
          
          {/* Spans columns 1-3 for the branching arrow structure */}
          <div className="col-span-3 flex justify-center h-14">
            <div className="flex">
              {/* Left branch to Pipelines - positioned over column 1 center */}
              <div className="flex flex-col items-center" style={{ width: '140px' }}>
                <div className={`w-0.5 flex-1 ${activePipelines.size > 0 ? 'bg-green-500' : 'bg-border'}`} />
                <div className={`w-0 h-0 border-l-[5px] border-r-[5px] border-t-[6px] ${
                  activePipelines.size > 0 ? 'border-t-green-500' : 'border-t-border'
                } border-l-transparent border-r-transparent`} />
              </div>
              
              {/* Horizontal connector bar (column 2 width) */}
              <div className="flex items-start" style={{ width: '48px' }}>
                <div className={`w-full h-0.5 mt-0 ${activePipelines.size > 0 ? 'bg-green-500' : 'bg-border'}`} />
              </div>
              
              {/* Center T-junction over AI Engine (column 3) */}
              <div className="flex flex-col items-center" style={{ width: '140px' }}>
                {/* Vertical line down to Local AI */}
                <div className={`w-0.5 flex-1 ${hasActiveCalls ? 'bg-green-500' : 'bg-border'}`} />
                <div className={`w-0 h-0 border-l-[5px] border-r-[5px] border-t-[6px] ${
                  hasActiveCalls ? 'border-t-green-500' : 'border-t-border'
                } border-l-transparent border-r-transparent`} />
              </div>
            </div>
          </div>
          
          {/* Column 4: Empty */}
          <div></div>
          
          {/* Column 5: Empty */}
          <div></div>

          {/* === ROW 3: Pipelines ← Local AI Server → Models === */}
          
          {/* Pipelines with sub-components */}
          <div>
            <div className="text-xs text-muted-foreground uppercase tracking-wide mb-2 text-center">Pipelines</div>
            {state.configuredPipelines.length === 0 ? (
              <div className="p-3 rounded-lg border border-dashed border-border text-xs text-muted-foreground text-center">
                No pipelines
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                {state.configuredPipelines.map(pipeline => {
                  const activeCount = activePipelines.get(pipeline.name) || 0;
                  const isActive = activeCount > 0;
                  const isDefault = pipeline.name === state.activePipeline;
                  
                  return (
                    <div key={pipeline.name} className="flex flex-col">
                      {/* Pipeline name header */}
                      <div 
                        className={`relative flex items-center gap-2 p-2 rounded-t-lg border border-b-0 transition-all ${
                          isActive 
                            ? 'border-green-500 bg-green-500/10' 
                            : 'border-border bg-card'
                        }`}
                      >
                        <Layers className={`w-4 h-4 flex-shrink-0 ${isActive ? 'text-green-500' : 'text-muted-foreground'}`} />
                        <span className={`text-xs font-medium truncate ${isActive ? 'text-green-500' : 'text-foreground'}`}>
                          {pipeline.name.replace(/_/g, ' ')}
                        </span>
                        {isDefault && <span className="text-yellow-500 text-[10px] ml-auto flex-shrink-0">⭐</span>}
                      </div>
                      {/* Pipeline components (STT/LLM/TTS) */}
                      <div className={`flex flex-col gap-0.5 p-1.5 rounded-b-lg border transition-all ${
                        isActive ? 'border-green-500 bg-green-500/5' : 'border-border bg-muted/30'
                      }`}>
                        {/* STT */}
                        <div className={`flex items-center gap-1.5 px-1.5 py-0.5 rounded text-[10px] ${
                          isActive ? 'text-green-500' : 'text-muted-foreground'
                        }`}>
                          <Mic className={`w-3 h-3 ${isActive ? 'text-green-500' : 'text-muted-foreground'}`} />
                          <span className="truncate">{pipeline.stt || 'N/A'}</span>
                        </div>
                        {/* LLM */}
                        <div className={`flex items-center gap-1.5 px-1.5 py-0.5 rounded text-[10px] ${
                          isActive ? 'text-green-500' : 'text-muted-foreground'
                        }`}>
                          <MessageSquare className={`w-3 h-3 ${isActive ? 'text-green-500' : 'text-muted-foreground'}`} />
                          <span className="truncate">{pipeline.llm || 'N/A'}</span>
                        </div>
                        {/* TTS */}
                        <div className={`flex items-center gap-1.5 px-1.5 py-0.5 rounded text-[10px] ${
                          isActive ? 'text-green-500' : 'text-muted-foreground'
                        }`}>
                          <Volume2 className={`w-3 h-3 ${isActive ? 'text-green-500' : 'text-muted-foreground'}`} />
                          <span className="truncate">{pipeline.tts || 'N/A'}</span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Arrow: Pipelines ← Local AI */}
          <div className="flex items-center justify-center self-center rotate-180">
            <div className={`w-0 h-0 border-t-[6px] border-b-[6px] border-l-[8px] ${
              activePipelines.size > 0 ? 'border-l-green-500' : 'border-l-border'
            } border-t-transparent border-b-transparent`} />
            <div className={`w-6 h-0.5 ${activePipelines.size > 0 ? 'bg-green-500' : 'bg-border'}`} />
          </div>

          {/* Local AI Server (aligned with AI Engine above) */}
          <div>
            <div className="text-xs text-muted-foreground uppercase tracking-wide mb-2 text-center">Local AI Server</div>
            <div className={`relative p-4 rounded-lg border-2 transition-all duration-300 ${
              state.localAIStatus === 'error'
                ? 'border-red-500 bg-red-500/10'
                : 'border-border bg-card'
            }`}>
              <div className="flex flex-col items-center gap-2">
                <Server className={`w-8 h-8 ${
                  state.localAIStatus === 'error' ? 'text-red-500' : 'text-muted-foreground'
                }`} />
                <div className="text-center">
                  <div className={`font-semibold ${
                    state.localAIStatus === 'error' ? 'text-red-500' : 'text-foreground'
                  }`}>Local AI</div>
                  <div className="text-xs text-muted-foreground">Server</div>
                </div>
                <div className="w-full pt-2 mt-2 border-t border-border/50">
                  <div className="flex items-center justify-center text-xs">
                    {state.localAIStatus === 'connected' ? (
                      <span className="flex items-center gap-1 text-green-500">
                        <CheckCircle2 className="w-3 h-3" /> Connected
                      </span>
                    ) : (
                      <span className="flex items-center gap-1 text-red-500">
                        <XCircle className="w-3 h-3" /> Disconnected
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Arrow: Local AI → Models */}
          <div className="flex items-center justify-center self-center">
            <div className="w-6 h-0.5 bg-border" />
            <div className="w-0 h-0 border-t-[6px] border-b-[6px] border-l-[8px] border-l-border border-t-transparent border-b-transparent" />
          </div>

          {/* STT / LLM / TTS Models */}
          <div>
            <div className="text-xs text-muted-foreground uppercase tracking-wide mb-2 text-center">Models</div>
            <div className="flex flex-col gap-2">
              {/* STT */}
              <div className={`flex items-center gap-2 p-2 px-3 rounded-lg border ${
                state.localAIModels?.stt?.loaded ? 'border-border bg-card' : 'border-border bg-muted/50'
              }`}>
                <Mic className={`w-4 h-4 ${state.localAIModels?.stt?.loaded ? 'text-green-500' : 'text-muted-foreground'}`} />
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium">STT</div>
                  <div className="text-[10px] text-muted-foreground truncate">
                    {getModelDisplayName(state.localAIModels?.stt, 'Not loaded')}
                  </div>
                </div>
                {state.localAIModels?.stt?.loaded ? (
                  <CheckCircle2 className="w-3.5 h-3.5 text-green-500 flex-shrink-0" />
                ) : (
                  <XCircle className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
                )}
              </div>

              {/* LLM */}
              <div className={`flex items-center gap-2 p-2 px-3 rounded-lg border ${
                state.localAIModels?.llm?.loaded ? 'border-border bg-card' : 'border-border bg-muted/50'
              }`}>
                <MessageSquare className={`w-4 h-4 ${state.localAIModels?.llm?.loaded ? 'text-green-500' : 'text-muted-foreground'}`} />
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium">LLM</div>
                  <div className="text-[10px] text-muted-foreground truncate">
                    {getModelDisplayName(state.localAIModels?.llm, 'Not loaded')}
                  </div>
                </div>
                {state.localAIModels?.llm?.loaded ? (
                  <CheckCircle2 className="w-3.5 h-3.5 text-green-500 flex-shrink-0" />
                ) : (
                  <XCircle className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
                )}
              </div>

              {/* TTS */}
              <div className={`flex items-center gap-2 p-2 px-3 rounded-lg border ${
                state.localAIModels?.tts?.loaded ? 'border-border bg-card' : 'border-border bg-muted/50'
              }`}>
                <Volume2 className={`w-4 h-4 ${state.localAIModels?.tts?.loaded ? 'text-green-500' : 'text-muted-foreground'}`} />
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium">TTS</div>
                  <div className="text-[10px] text-muted-foreground truncate">
                    {getModelDisplayName(state.localAIModels?.tts, 'Not loaded')}
                  </div>
                </div>
                {state.localAIModels?.tts?.loaded ? (
                  <CheckCircle2 className="w-3.5 h-3.5 text-green-500 flex-shrink-0" />
                ) : (
                  <XCircle className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Legend */}
        <div className="flex items-center justify-center gap-4 pt-4 mt-4 border-t border-border text-[10px] text-muted-foreground flex-wrap">
          <div className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full bg-green-500" />
            <span>Ready</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full bg-orange-500" />
            <span>Disabled</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full bg-red-500" />
            <span>Not Ready</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-yellow-500">⭐</span>
            <span>Default</span>
          </div>
        </div>
      </div>

      {/* CSS for flow animation */}
      <style>{`
        @keyframes flow {
          0% { transform: translateX(-16px); }
          100% { transform: translateX(32px); }
        }
        .animate-flow {
          animation: flow 0.8s linear infinite;
        }
      `}</style>
    </div>
  );
};

export default SystemTopology;
