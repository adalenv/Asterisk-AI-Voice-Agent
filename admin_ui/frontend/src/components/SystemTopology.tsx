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
          for (const [name] of Object.entries(parsed.providers)) {
            // Only include full agent providers, skip modular components like local_stt, groq_llm, etc.
            if (FULL_AGENT_PROVIDERS.has(name)) {
              providers.push({
                name,
                displayName: PROVIDER_DISPLAY_NAMES[name] || name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
              });
            }
          }
        }

        // Extract pipelines
        const pipelines: PipelineConfig[] = [];
        if (parsed?.pipelines && typeof parsed.pipelines === 'object') {
          for (const [name, config] of Object.entries(parsed.pipelines)) {
            const cfg = config as any;
            pipelines.push({
              name,
              stt: cfg?.stt?.provider,
              llm: cfg?.llm?.provider,
              tts: cfg?.tts?.provider,
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

  // Poll for active calls from logs
  useEffect(() => {
    const fetchCallEvents = async () => {
      try {
        const res = await axios.get('/api/logs/ai_engine/events', {
          params: { limit: 100, since: '60s' }
        });
        
        const events = res.data.events || [];
        const calls = new Map<string, CallState>(state.activeCalls);
        const now = new Date();
        
        // Track which calls we've seen end
        const endedCalls = new Set<string>();
        
        for (const event of events) {
          const msg = (event.msg || '').toLowerCase();
          const callId = event.call_id;
          
          if (!callId) continue;
          
          // Detect call start
          if (msg.includes('stasisstart') || msg.includes('stasis start')) {
            if (!calls.has(callId) && !endedCalls.has(callId)) {
              calls.set(callId, {
                call_id: callId,
                started_at: event.ts ? new Date(event.ts) : now,
                state: 'arriving',
              });
            }
          }
          
          // Detect provider assignment
          if (msg.includes('audio profile resolved') || msg.includes('provider selected') || msg.includes('using provider')) {
            const call = calls.get(callId);
            if (call) {
              call.provider = event.provider || call.provider;
              call.state = 'connected';
            }
          }
          
          // Detect pipeline usage
          if (msg.includes('pipeline') && event.pipeline) {
            const call = calls.get(callId);
            if (call) {
              call.pipeline = event.pipeline;
            }
          }
          
          // Detect call end
          if (msg.includes('stasis ended') || msg.includes('call cleanup') || msg.includes('channel destroyed') || msg.includes('hangup')) {
            endedCalls.add(callId);
            calls.delete(callId);
          }
        }
        
        // Clean up stale calls (older than 5 minutes)
        const fiveMinutesAgo = new Date(now.getTime() - 5 * 60 * 1000);
        for (const [callId, call] of calls) {
          if (call.started_at < fiveMinutesAgo) {
            calls.delete(callId);
          }
        }
        
        setState(prev => ({ ...prev, activeCalls: calls }));
      } catch (err) {
        console.error('Failed to fetch call events', err);
      }
    };
    
    fetchCallEvents();
    const interval = setInterval(fetchCallEvents, 2000);
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
        {/* Top Row: Asterisk → AI Engine → Providers */}
        <div className="flex items-start justify-center gap-3">
          {/* Asterisk PBX */}
          <div className={`relative w-[140px] p-4 rounded-lg border-2 transition-all duration-300 ${
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

          {/* Arrow 1 */}
          <div className="flex items-center self-center mt-8">
            <div className={`w-8 h-0.5 ${hasActiveCalls ? 'bg-green-500' : 'bg-border'} relative overflow-hidden`}>
              {hasActiveCalls && (
                <div className="absolute inset-y-0 w-4 bg-green-300 animate-flow" />
              )}
            </div>
            <div className={`w-0 h-0 border-t-[6px] border-b-[6px] border-l-[8px] ${
              hasActiveCalls ? 'border-l-green-500' : 'border-l-border'
            } border-t-transparent border-b-transparent`} />
          </div>

          {/* AI Engine Core */}
          <div className={`relative w-[140px] p-4 rounded-lg border-2 transition-all duration-300 ${
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

          {/* Arrow 2 */}
          <div className="flex items-center self-center mt-8">
            <div className={`w-8 h-0.5 ${hasActiveCalls ? 'bg-green-500' : 'bg-border'} relative overflow-hidden`}>
              {hasActiveCalls && (
                <div className="absolute inset-y-0 w-4 bg-green-300 animate-flow" />
              )}
            </div>
            <div className={`w-0 h-0 border-t-[6px] border-b-[6px] border-l-[8px] ${
              hasActiveCalls ? 'border-l-green-500' : 'border-l-border'
            } border-t-transparent border-b-transparent`} />
          </div>

          {/* Providers (Full Agents Only) */}
          <div className="w-[140px]">
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
                  
                  return (
                    <div 
                      key={provider.name}
                      className={`relative flex items-center gap-2 p-2 px-3 rounded-lg border transition-all duration-300 ${
                        isActive 
                          ? 'border-green-500 bg-green-500/10 shadow-md shadow-green-500/20' 
                          : 'border-border bg-card'
                      }`}
                    >
                      {isActive && (
                        <div className="absolute inset-0 rounded-lg border border-green-500 animate-ping opacity-20" />
                      )}
                      <Zap className={`w-4 h-4 flex-shrink-0 ${isActive ? 'text-green-500' : 'text-muted-foreground'}`} />
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
        </div>

        {/* Bottom Row: Pipelines → Local AI Server → Models */}
        <div className="flex items-start justify-center gap-3 mt-6 pt-6 border-t border-border">
          {/* Pipelines */}
          <div className="w-[140px]">
            <div className="text-xs text-muted-foreground uppercase tracking-wide mb-2 text-center">Pipelines</div>
            {state.configuredPipelines.length === 0 ? (
              <div className="p-3 rounded-lg border border-dashed border-border text-xs text-muted-foreground text-center">
                No pipelines
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                {state.configuredPipelines.map(pipeline => {
                  const activeCount = activePipelines.get(pipeline.name) || 0;
                  const isActive = activeCount > 0;
                  const isDefault = pipeline.name === state.activePipeline;
                  
                  return (
                    <div 
                      key={pipeline.name}
                      className={`relative flex items-center gap-2 p-2 rounded-lg border transition-all ${
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
                  );
                })}
              </div>
            )}
          </div>

          {/* Arrow to Local AI */}
          <div className="flex items-center self-center mt-6">
            <div className="w-8 h-0.5 bg-border" />
            <div className="w-0 h-0 border-t-[6px] border-b-[6px] border-l-[8px] border-l-border border-t-transparent border-b-transparent" />
          </div>

          {/* Local AI Server (same size as AI Engine) */}
          <div className="w-[140px]">
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

          {/* Arrow to STT/LLM/TTS */}
          <div className="flex items-center h-full pt-8">
            <div className="w-8 h-0.5 bg-border" />
            <div className="w-0 h-0 border-t-[6px] border-b-[6px] border-l-[8px] border-l-border border-t-transparent border-b-transparent" />
          </div>

          {/* STT / LLM / TTS Components */}
          <div className="flex flex-col">
            <div className="text-xs text-muted-foreground uppercase tracking-wide mb-2 text-center">Models</div>
            <div className="flex flex-col gap-2">
              {/* STT */}
              <div className={`flex items-center gap-2 p-2 px-3 rounded-lg border ${
                state.localAIModels?.stt?.loaded ? 'border-border bg-card' : 'border-border bg-muted/50'
              }`}>
                <Mic className={`w-4 h-4 ${state.localAIModels?.stt?.loaded ? 'text-green-500' : 'text-muted-foreground'}`} />
                <div className="flex-1">
                  <div className="text-xs font-medium">STT</div>
                  <div className="text-[10px] text-muted-foreground">
                    {getModelDisplayName(state.localAIModels?.stt, 'Not loaded')}
                  </div>
                </div>
                {state.localAIModels?.stt?.loaded ? (
                  <CheckCircle2 className="w-3.5 h-3.5 text-green-500" />
                ) : (
                  <XCircle className="w-3.5 h-3.5 text-muted-foreground" />
                )}
              </div>

              {/* LLM */}
              <div className={`flex items-center gap-2 p-2 px-3 rounded-lg border ${
                state.localAIModels?.llm?.loaded ? 'border-border bg-card' : 'border-border bg-muted/50'
              }`}>
                <MessageSquare className={`w-4 h-4 ${state.localAIModels?.llm?.loaded ? 'text-green-500' : 'text-muted-foreground'}`} />
                <div className="flex-1">
                  <div className="text-xs font-medium">LLM</div>
                  <div className="text-[10px] text-muted-foreground">
                    {getModelDisplayName(state.localAIModels?.llm, 'Not loaded')}
                  </div>
                </div>
                {state.localAIModels?.llm?.loaded ? (
                  <CheckCircle2 className="w-3.5 h-3.5 text-green-500" />
                ) : (
                  <XCircle className="w-3.5 h-3.5 text-muted-foreground" />
                )}
              </div>

              {/* TTS */}
              <div className={`flex items-center gap-2 p-2 px-3 rounded-lg border ${
                state.localAIModels?.tts?.loaded ? 'border-border bg-card' : 'border-border bg-muted/50'
              }`}>
                <Volume2 className={`w-4 h-4 ${state.localAIModels?.tts?.loaded ? 'text-green-500' : 'text-muted-foreground'}`} />
                <div className="flex-1">
                  <div className="text-xs font-medium">TTS</div>
                  <div className="text-[10px] text-muted-foreground">
                    {getModelDisplayName(state.localAIModels?.tts, 'Not loaded')}
                  </div>
                </div>
                {state.localAIModels?.tts?.loaded ? (
                  <CheckCircle2 className="w-3.5 h-3.5 text-green-500" />
                ) : (
                  <XCircle className="w-3.5 h-3.5 text-muted-foreground" />
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Legend */}
        <div className="flex items-center justify-center gap-6 pt-4 mt-4 border-t border-border text-[10px] text-muted-foreground">
          <div className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full bg-green-500 animate-pulse" />
            <span>Active</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full border border-border bg-card" />
            <span>Ready</span>
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
