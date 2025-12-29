import React, { useState, useEffect } from 'react';
import { FormInput, FormLabel } from '../ui/FormComponents';
import { ensureModularKey, isFullAgentProvider, isRegisteredProvider } from '../../utils/providerNaming';
import { CheckCircle, AlertCircle, Loader2, Wrench } from 'lucide-react';

// Available tools that can be enabled for pipelines
const AVAILABLE_TOOLS = [
    { id: 'transfer', label: 'Transfer Call', description: 'Transfer to extensions, queues, or ring groups' },
    { id: 'attended_transfer', label: 'Attended Transfer', description: 'Warm transfer with agent announcement + DTMF accept/decline (requires Local AI Server)' },
    { id: 'cancel_transfer', label: 'Cancel Transfer', description: 'Cancel an in-progress transfer' },
    { id: 'hangup_call', label: 'Hangup Call', description: 'End the call with a farewell message' },
    { id: 'leave_voicemail', label: 'Leave Voicemail', description: 'Send caller to voicemail' },
    { id: 'send_email_summary', label: 'Email Summary', description: 'Email call summary after hangup' },
    { id: 'request_transcript', label: 'Request Transcript', description: 'Email transcript to caller' },
];

interface LocalAIStatus {
    stt_backend?: string;
    stt_model?: string;
    tts_backend?: string;
    tts_voice?: string;
    llm_model?: string;
    healthy?: boolean;
}

interface PipelineFormProps {
    config: any;
    providers: any;
    onChange: (newConfig: any) => void;
    isNew?: boolean;
}

const PipelineForm: React.FC<PipelineFormProps> = ({ config, providers, onChange, isNew }) => {
    const [localConfig, setLocalConfig] = useState<any>({ ...config });
    const [localAIStatus, setLocalAIStatus] = useState<LocalAIStatus | null>(null);
    const [statusLoading, setStatusLoading] = useState(false);

    // Fetch local AI server status for backend info (AAVA-116)
    useEffect(() => {
        const fetchLocalAIStatus = async () => {
            setStatusLoading(true);
            try {
                const response = await fetch('/api/local-ai/status');
                if (response.ok) {
                    const data = await response.json();
                    setLocalAIStatus(data);
                }
            } catch (error) {
                console.error('Failed to fetch local AI status:', error);
            } finally {
                setStatusLoading(false);
            }
        };
        fetchLocalAIStatus();
    }, []);

    useEffect(() => {
        setLocalConfig({ ...config });
    }, [config]);

    const updateConfig = (updates: any) => {
        const newConfig = { ...localConfig, ...updates };
        setLocalConfig(newConfig);
        onChange(newConfig);
    };

    // Helper to filter providers by capability
    // STRICT: Only use capabilities array. NO name matching.
    // Only show registered providers that have engine adapter support.
    const getProvidersByCapability = (cap: 'stt' | 'llm' | 'tts') => {
        return Object.entries(providers || {})
            .filter(([_, p]: [string, any]) => {
                // Exclude Full Agents from modular slots
                if (isFullAgentProvider(p)) return false;

                // Exclude unregistered providers (no engine adapter)
                if (!isRegisteredProvider(p)) return false;

                // Check capability existence
                return (p.capabilities || []).includes(cap);
            })
            .map(([name, p]: [string, any]) => ({
                value: name,
                label: name,
                disabled: p.enabled === false
            }));
    };

    const sttProviders = getProvidersByCapability('stt');
    const llmProviders = getProvidersByCapability('llm');
    const ttsProviders = getProvidersByCapability('tts');

    const handleProviderChange = (cap: 'stt' | 'llm' | 'tts', value: string) => {
        if (!value) {
            updateConfig({ [cap]: '' });
            return;
        }
        const normalized = ensureModularKey(value, cap);
        updateConfig({ [cap]: normalized });
    };

    return (
        <div className="space-y-6">
            <div className="space-y-4 border-b border-border pb-6">
                <h4 className="font-semibold">Pipeline Identity</h4>
                <FormInput
                    label="Pipeline Name"
                    value={localConfig.name || ''}
                    onChange={(e) => updateConfig({ name: e.target.value })}
                    placeholder="e.g., english_support"
                    disabled={!isNew}
                    tooltip="Unique identifier for this pipeline."
                />
            </div>

            <div className="space-y-4">
                <h4 className="font-semibold">Components</h4>

                <div className="space-y-2">
                    <FormLabel>Speech-to-Text (STT)</FormLabel>
                    <select
                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                        value={localConfig.stt || ''}
                        onChange={(e) => handleProviderChange('stt', e.target.value)}
                    >
                        <option value="">Select STT Provider...</option>
                        {sttProviders.map(p => (
                            <option key={p.value} value={p.value} disabled={p.disabled}>
                                {p.label} {p.disabled ? '(Disabled)' : ''}
                            </option>
                        ))}
                    </select>
                    {/* AAVA-116: Show active backend for local_stt */}
                    {localConfig.stt?.includes('local') && localAIStatus && (
                        <div className="flex items-center gap-2 text-xs text-muted-foreground bg-muted/50 px-3 py-2 rounded-md">
                            {statusLoading ? (
                                <Loader2 className="h-3 w-3 animate-spin" />
                            ) : localAIStatus.healthy ? (
                                <CheckCircle className="h-3 w-3 text-green-500" />
                            ) : (
                                <AlertCircle className="h-3 w-3 text-yellow-500" />
                            )}
                            <span>
                                Active Backend: <strong className="text-foreground">{localAIStatus.stt_backend || 'Unknown'}</strong>
                                {localAIStatus.stt_model && <span className="text-muted-foreground"> ({localAIStatus.stt_model})</span>}
                            </span>
                        </div>
                    )}
                    {sttProviders.length === 0 && (
                        <p className="text-xs text-destructive">No STT providers available. Create a modular STT provider first.</p>
                    )}
                </div>

                <div className="space-y-2">
                    <FormLabel>Large Language Model (LLM)</FormLabel>
                    <select
                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                        value={localConfig.llm || ''}
                        onChange={(e) => handleProviderChange('llm', e.target.value)}
                    >
                        <option value="">Select LLM Provider...</option>
                        {llmProviders.map(p => (
                            <option key={p.value} value={p.value} disabled={p.disabled}>
                                {p.label} {p.disabled ? '(Disabled)' : ''}
                            </option>
                        ))}
                    </select>
                    {llmProviders.length === 0 && (
                        <p className="text-xs text-destructive">No LLM providers available. Create a modular LLM provider first.</p>
                    )}
                </div>

                <div className="space-y-2">
                    <FormLabel>Text-to-Speech (TTS)</FormLabel>
                    <select
                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                        value={localConfig.tts || ''}
                        onChange={(e) => handleProviderChange('tts', e.target.value)}
                    >
                        <option value="">Select TTS Provider...</option>
                        {ttsProviders.map(p => (
                            <option key={p.value} value={p.value} disabled={p.disabled}>
                                {p.label} {p.disabled ? '(Disabled)' : ''}
                            </option>
                        ))}
                    </select>
                    {/* AAVA-116: Show active backend for local_tts */}
                    {localConfig.tts?.includes('local') && localAIStatus && (
                        <div className="flex items-center gap-2 text-xs text-muted-foreground bg-muted/50 px-3 py-2 rounded-md">
                            {statusLoading ? (
                                <Loader2 className="h-3 w-3 animate-spin" />
                            ) : localAIStatus.healthy ? (
                                <CheckCircle className="h-3 w-3 text-green-500" />
                            ) : (
                                <AlertCircle className="h-3 w-3 text-yellow-500" />
                            )}
                            <span>
                                Active Backend: <strong className="text-foreground">{localAIStatus.tts_backend || 'Unknown'}</strong>
                                {localAIStatus.tts_voice && <span className="text-muted-foreground"> ({localAIStatus.tts_voice})</span>}
                            </span>
                        </div>
                    )}
                    {ttsProviders.length === 0 && (
                        <p className="text-xs text-destructive">No TTS providers available. Create a modular TTS provider first.</p>
                    )}
                </div>
            </div>

            <div className="space-y-4 border-t border-border pt-6">
                <div className="flex items-center gap-2">
                    <Wrench className="h-4 w-4 text-muted-foreground" />
                    <h4 className="font-semibold">Tool Capabilities</h4>
                </div>
                <p className="text-sm text-muted-foreground">
                    Select which tools the AI can use during calls. These enable actions like transferring calls and sending emails.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {AVAILABLE_TOOLS.map((tool) => {
                        const isEnabled = (localConfig.tools || []).includes(tool.id);
                        return (
                            <label
                                key={tool.id}
                                className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${
                                    isEnabled 
                                        ? 'border-primary bg-primary/5' 
                                        : 'border-border hover:border-muted-foreground/50'
                                }`}
                            >
                                <input
                                    type="checkbox"
                                    checked={isEnabled}
                                    onChange={(e) => {
                                        const currentTools = localConfig.tools || [];
                                        if (e.target.checked) {
                                            updateConfig({ tools: [...currentTools, tool.id] });
                                        } else {
                                            updateConfig({ tools: currentTools.filter((t: string) => t !== tool.id) });
                                        }
                                    }}
                                    className="mt-1 h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary"
                                />
                                <div className="flex-1">
                                    <div className="font-medium text-sm">{tool.label}</div>
                                    <div className="text-xs text-muted-foreground">{tool.description}</div>
                                </div>
                            </label>
                        );
                    })}
                </div>
                <p className="text-xs text-muted-foreground">
                    <strong>Note:</strong> Some LLM providers (e.g., Groq) may not support tool calling. Check provider documentation.
                </p>
            </div>
        </div>
    );
};

export default PipelineForm;
