import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { AlertCircle } from 'lucide-react';

interface LocalProviderFormProps {
    config: any;
    onChange: (newConfig: any) => void;
}

const LocalProviderForm: React.FC<LocalProviderFormProps> = ({ config, onChange }) => {
    const [modelCatalog, setModelCatalog] = useState<any>({ stt: [], llm: [], tts: [] });
    const [loading, setLoading] = useState(true);
    const [fetchError, setFetchError] = useState<string | null>(null);

    useEffect(() => {
        const fetchModels = async () => {
            try {
                // Fetch installed models from local_ai API
                const res = await axios.get('/api/local-ai/models');
                const data = res.data;

                // Flatten STT models (Dict[backend, List[Model]]) -> List[Model]
                const sttModels = Object.values(data.stt || {}).flat();

                // Flatten TTS models (Dict[backend, List[Model]])
                const ttsModels = Object.values(data.tts || {}).flat();

                setModelCatalog({
                    stt: sttModels,
                    llm: data.llm || [],
                    tts: ttsModels
                });
            } catch (err) {
                console.error("Failed to fetch local models", err);
                setFetchError("Could not load installed models. Ensure AI Engine is running.");
            } finally {
                setLoading(false);
            }
        };

        fetchModels();
    }, []);

    const handleChange = (field: string, value: any) => {
        // If changing model backend, also try to set a sane default model path if available
        if (field === 'stt_backend') {
            // Logic to auto-select recommended model could go here, but for now just change backend
        }
        onChange({ ...config, [field]: value });
    };

    const name = (config?.name || '').toLowerCase();
    const caps = config?.capabilities || [];
    const isFullAgent = (caps.includes('stt') && caps.includes('llm') && caps.includes('tts'));

    // For modular providers, detect role by name or capability
    const isSTT = isFullAgent || name.includes('stt') || caps.includes('stt');
    const isTTS = isFullAgent || name.includes('tts') || caps.includes('tts');
    const isLLM = isFullAgent || name.includes('llm') || caps.includes('llm') || (!name.includes('stt') && !name.includes('tts'));

    // Helpers to find model details
    const getModelPathPlaceholder = (backend: string, type: 'stt' | 'tts') => {
        if (loading) return "Loading...";
        if (backend === 'vosk') return '/app/models/stt/vosk-model-en-us-0.22';
        if (backend === 'sherpa') return '/app/models/stt/sherpa-onnx-streaming-zipformer-en-2023-06-26';
        if (backend === 'piper') return '/app/models/tts/en_US-lessac-medium.onnx';
        if (backend === 'kokoro') return '/app/models/tts/kokoro';
        return '';
    };

    return (
        <div className="space-y-6">
            {/* Full Agent Notice */}
            {isFullAgent && (
                <div className="bg-green-50/50 dark:bg-green-900/10 p-3 rounded-md border border-green-200 dark:border-green-900/30 text-sm text-green-800 dark:text-green-300">
                    <strong>Full Agent Mode:</strong> This provider handles STT, LLM, and TTS together via Local AI Server.
                </div>
            )}

            {/* Error Banner */}
            {fetchError && (
                <div className="bg-red-50 dark:bg-red-900/10 p-3 rounded-md border border-red-200 dark:border-red-900/30 text-sm text-red-600 dark:text-red-400 flex items-center gap-2">
                    <AlertCircle className="w-4 h-4" />
                    {fetchError}
                </div>
            )}

            {/* Greeting (for full agents) */}
            {isFullAgent && (
                <div>
                    <h4 className="font-semibold mb-3">Greeting</h4>
                    <div className="space-y-2">
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.greeting || ''}
                            onChange={(e) => handleChange('greeting', e.target.value)}
                            placeholder="Hello! I'm your local AI assistant."
                        />
                        <p className="text-xs text-muted-foreground">
                            Initial greeting message spoken when a call starts.
                        </p>
                    </div>
                </div>
            )}

            {/* Connection Settings */}
            <div>
                <h4 className="font-semibold mb-3">Connection Settings</h4>
                <div className="space-y-2">
                    <label className="text-sm font-medium">
                        {isFullAgent ? 'Base URL / WebSocket URL' : 'WebSocket URL'}
                        <span className="text-xs text-muted-foreground ml-2">({isFullAgent ? 'base_url' : 'ws_url'})</span>
                    </label>
                    <input
                        type="text"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={isFullAgent
                            ? (config.base_url || '${LOCAL_WS_URL:-ws://local_ai_server:8765}')
                            : (config.ws_url || '${LOCAL_WS_URL:-ws://local_ai_server:8765}')}
                        onChange={(e) => handleChange(isFullAgent ? 'base_url' : 'ws_url', e.target.value)}
                        placeholder="${LOCAL_WS_URL:-ws://local_ai_server:8765}"
                    />
                    <p className="text-xs text-muted-foreground">
                        WebSocket URL for local AI server. Change port if running on custom configuration.
                    </p>
                </div>
            </div>

            {/* Connection Parameters */}
            <div>
                <h4 className="font-semibold mb-3">Connection Parameters</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Connect Timeout (s)</label>
                        <input
                            type="number"
                            step="0.1"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.connect_timeout_sec || 5.0}
                            onChange={(e) => handleChange('connect_timeout_sec', parseFloat(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Response Timeout (s)</label>
                        <input
                            type="number"
                            step="0.1"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.response_timeout_sec || 5.0}
                            onChange={(e) => handleChange('response_timeout_sec', parseFloat(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Farewell Timeout (s)</label>
                        <input
                            type="number"
                            step="1"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.farewell_timeout_sec || 30.0}
                            onChange={(e) => handleChange('farewell_timeout_sec', parseFloat(e.target.value))}
                        />
                        <p className="text-xs text-muted-foreground">
                            Time to wait for goodbye TTS. Set based on LLM warmup time (check logs).
                        </p>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Chunk Size (ms)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.chunk_ms || 200}
                            onChange={(e) => handleChange('chunk_ms', parseInt(e.target.value))}
                        />
                    </div>
                </div>
            </div>

            {/* STT Backend Settings */}
            {isSTT && (
                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">STT (Speech-to-Text)</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">STT Backend</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.stt_backend || 'vosk'}
                                onChange={(e) => handleChange('stt_backend', e.target.value)}
                            >
                                <option value="vosk">Vosk (Local)</option>
                                <option value="kroko">Kroko</option>
                                <option value="sherpa">Sherpa-ONNX (Local)</option>
                            </select>
                        </div>

                        {/* Vosk settings */}
                        {config.stt_backend === 'vosk' && (
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Vosk Model Path</label>
                                <div className="relative">
                                    <input
                                        type="text"
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={config.stt_model || ''}
                                        onChange={(e) => handleChange('stt_model', e.target.value)}
                                        placeholder={getModelPathPlaceholder('vosk', 'stt')}
                                    />
                                    {/* Quick Select for Vosk Models */}
                                    {modelCatalog.stt.some((m: any) => m.id === 'vosk') && (
                                        <div className="mt-1 text-xs text-muted-foreground">
                                            Available: {modelCatalog.stt.filter((m: any) => m.id === 'vosk').map((m: any) => (
                                                <button
                                                    key={m.id}
                                                    type="button"
                                                    className="underline mr-2 text-primary"
                                                    onClick={() => handleChange('stt_model', m.model_path)}
                                                >
                                                    {m.model_path}
                                                </button>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            </div>
                        )}

                        {/* Sherpa settings */}
                        {config.stt_backend === 'sherpa' && (
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Sherpa Model Path</label>
                                <input
                                    type="text"
                                    className="w-full p-2 rounded border border-input bg-background"
                                    value={config.sherpa_model_path || ''}
                                    onChange={(e) => handleChange('sherpa_model_path', e.target.value)}
                                    placeholder={getModelPathPlaceholder('sherpa', 'stt')}
                                />
                                {modelCatalog.stt.some((m: any) => m.id.includes('sherpa')) && (
                                    <div className="mt-1 text-xs text-muted-foreground">
                                        Available: {modelCatalog.stt.filter((m: any) => m.id.includes('sherpa')).map((m: any) => (
                                            <button
                                                key={m.id}
                                                type="button"
                                                className="underline mr-2 text-primary"
                                                onClick={() => handleChange('sherpa_model_path', m.model_path)}
                                            >
                                                {m.model_path}
                                            </button>
                                        ))}
                                    </div>
                                )}
                            </div>
                        )}

                        {/* Kroko settings - Cloud or Local */}
                        {config.stt_backend === 'kroko' && (
                            <>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Kroko Model</label>
                                    <div className="relative">
                                        <input
                                            type="text"
                                            className="w-full p-2 rounded border border-input bg-background"
                                            value={config.stt_model || ''}
                                            onChange={(e) => handleChange('stt_model', e.target.value)}
                                            placeholder="Leave empty for Cloud, or enter path for Embedded"
                                        />
                                        {/* Quick Select for Kroko Models */}
                                        {modelCatalog.stt.some((m: any) => m.backend === 'kroko') && (
                                            <div className="mt-1 text-xs text-muted-foreground">
                                                Available: {modelCatalog.stt
                                                    .filter((m: any) => m.backend === 'kroko')
                                                    .map((m: any) => (
                                                        <button
                                                            key={m.id}
                                                            type="button"
                                                            className="underline mr-2 text-primary"
                                                            onClick={() => {
                                                                if (m.id === 'kroko_cloud') {
                                                                    handleChange('stt_model', '');
                                                                    handleChange('kroko_url', m.path);
                                                                } else {
                                                                    handleChange('stt_model', m.path);
                                                                }
                                                            }}
                                                        >
                                                            {m.name}
                                                        </button>
                                                    ))}
                                            </div>
                                        )}
                                    </div>
                                </div>

                                {/* Only show URL/Key if NO local model path is set (Cloud Mode) */}
                                {!config.stt_model && (
                                    <>
                                        <div className="space-y-2">
                                            <label className="text-sm font-medium">Kroko URL</label>
                                            <input
                                                type="text"
                                                className="w-full p-2 rounded border border-input bg-background"
                                                value={config.kroko_url || 'wss://app.kroko.ai/api/v1/transcripts/streaming'}
                                                onChange={(e) => handleChange('kroko_url', e.target.value)}
                                            />
                                        </div>
                                        <div className="space-y-2">
                                            <label className="text-sm font-medium">Kroko API Key</label>
                                            <input
                                                type="password"
                                                className="w-full p-2 rounded border border-input bg-background"
                                                value={config.kroko_api_key || ''}
                                                onChange={(e) => handleChange('kroko_api_key', e.target.value)}
                                                placeholder="Your Kroko API key"
                                            />
                                        </div>
                                    </>
                                )}

                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Language</label>
                                    <select
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={config.kroko_language || 'en-US'}
                                        onChange={(e) => handleChange('kroko_language', e.target.value)}
                                    >
                                        <option value="en-US">English (US)</option>
                                        <option value="en-GB">English (UK)</option>
                                        <option value="es-ES">Spanish</option>
                                        <option value="fr-FR">French</option>
                                        <option value="de-DE">German</option>
                                    </select>
                                </div>
                            </>
                        )}
                    </div>
                </div>
            )}

            {/* TTS Backend Settings */}
            {isTTS && (
                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">TTS (Text-to-Speech)</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">TTS Backend</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.tts_backend || 'piper'}
                                onChange={(e) => handleChange('tts_backend', e.target.value)}
                            >
                                <option value="piper">Piper (Local)</option>
                                <option value="kokoro">Kokoro (Local, Premium)</option>
                            </select>
                        </div>

                        {/* Piper settings */}
                        {(config.tts_backend || 'piper') === 'piper' && (
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Piper Voice Path</label>
                                <input
                                    type="text"
                                    className="w-full p-2 rounded border border-input bg-background"
                                    value={config.tts_voice || ''}
                                    onChange={(e) => handleChange('tts_voice', e.target.value)}
                                    placeholder={getModelPathPlaceholder('piper', 'tts')}
                                />
                                {modelCatalog.tts.some((m: any) => m.id.includes('piper')) && (
                                    <div className="mt-1 text-xs text-muted-foreground flex flex-wrap gap-2">
                                        <span>Use:</span>
                                        {modelCatalog.tts.filter((m: any) => m.id.includes('piper')).map((m: any) => (
                                            <button
                                                key={m.id}
                                                type="button"
                                                className="underline text-primary"
                                                onClick={() => handleChange('tts_voice', m.model_path)}
                                                title={m.name}
                                            >
                                                {m.id.replace('piper_', '')}
                                            </button>
                                        ))}
                                    </div>
                                )}
                            </div>
                        )}

                        {/* Kokoro settings */}
                        {config.tts_backend === 'kokoro' && (
                            <>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Voice</label>
                                    <select
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={config.kokoro_voice || 'af_heart'}
                                        onChange={(e) => handleChange('kokoro_voice', e.target.value)}
                                    >
                                        {/* Use backend voice files list if available, else fallback */}
                                        {modelCatalog.tts.find((m: any) => m.id === 'kokoro_82m')?.voice_files
                                            ? Object.keys(modelCatalog.tts.find((m: any) => m.id === 'kokoro_82m').voice_files).map((v: string) => (
                                                <option key={v} value={v}>{v}</option>
                                            ))
                                            : (
                                                <>
                                                    <option value="af_heart">Heart (Female, American)</option>
                                                    <option value="af_bella">Bella (Female, American)</option>
                                                    <option value="af_nicole">Nicole (Female, American)</option>
                                                    <option value="af_sarah">Sarah (Female, American)</option>
                                                    <option value="af_sky">Sky (Female, American)</option>
                                                    <option value="am_adam">Adam (Male, American)</option>
                                                    <option value="am_michael">Michael (Male, American)</option>
                                                    <option value="bf_emma">Emma (Female, British)</option>
                                                    <option value="bf_isabella">Isabella (Female, British)</option>
                                                    <option value="bm_george">George (Male, British)</option>
                                                    <option value="bm_lewis">Lewis (Male, British)</option>
                                                </>
                                            )
                                        }
                                    </select>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Model Path</label>
                                    <input
                                        type="text"
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={config.kokoro_model_path || ''}
                                        onChange={(e) => handleChange('kokoro_model_path', e.target.value)}
                                        placeholder={getModelPathPlaceholder('kokoro', 'tts')}
                                    />
                                    {modelCatalog.tts.some((m: any) => m.id === 'kokoro_82m') && (
                                        <div className="mt-1 text-xs text-muted-foreground">
                                            Available: {modelCatalog.tts.filter((m: any) => m.id === 'kokoro_82m').map((m: any) => (
                                                <button
                                                    key={m.id}
                                                    type="button"
                                                    className="underline mr-2 text-primary"
                                                    onClick={() => handleChange('kokoro_model_path', m.model_path)}
                                                >
                                                    {m.model_path}
                                                </button>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            </>
                        )}
                    </div>
                </div>
            )}

            {/* LLM Settings */}
            {isLLM && (
                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">LLM (Large Language Model)</h4>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Max Tokens</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.max_tokens || 150}
                            onChange={(e) => handleChange('max_tokens', parseInt(e.target.value))}
                        />
                        <p className="text-xs text-muted-foreground">
                            Uses local model configured via Environment variables.
                        </p>
                    </div>
                </div>
            )}

            <div className="flex items-center space-x-2">
                <input
                    type="checkbox"
                    id="enabled"
                    className="rounded border-input"
                    checked={config.enabled ?? true}
                    onChange={(e) => handleChange('enabled', e.target.checked)}
                />
                <label htmlFor="enabled" className="text-sm font-medium">Enabled</label>
            </div>
        </div>
    );
};

export default LocalProviderForm;
