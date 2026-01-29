import React from 'react';

interface OpenAIRealtimeProviderFormProps {
    config: any;
    onChange: (newConfig: any) => void;
}

const OpenAIRealtimeProviderForm: React.FC<OpenAIRealtimeProviderFormProps> = ({ config, onChange }) => {
    const handleChange = (field: string, value: any) => {
        onChange({ ...config, [field]: value });
    };

    const handleNestedChange = (parent: string, field: string, value: any) => {
        onChange({
            ...config,
            [parent]: {
                ...config[parent],
                [field]: value
            }
        });
    };

    return (
        <div className="space-y-6">
            {/* Base URL Section */}
            <div>
                <h4 className="font-semibold mb-3">API Endpoint</h4>
                <div className="space-y-2">
                    <label className="text-sm font-medium">
                        Realtime Base URL
                        <span className="text-xs text-muted-foreground ml-2">(base_url)</span>
                    </label>
                    <input
                        type="text"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.base_url || 'wss://api.openai.com/v1/realtime'}
                        onChange={(e) => handleChange('base_url', e.target.value)}
                        placeholder="wss://api.openai.com/v1/realtime"
                    />
                    <p className="text-xs text-muted-foreground">
                        WebSocket endpoint for OpenAI Realtime API. Change for Azure OpenAI or compatible services.
                    </p>
                </div>
            </div>

            {/* Model & Voice Section */}
            <div>
                <h4 className="font-semibold mb-3">Model & Voice</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Model</label>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.model || 'gpt-4o-realtime-preview'}
                            onChange={(e) => handleChange('model', e.target.value)}
                        >
                            <optgroup label="GPT-4o Realtime">
                                <option value="gpt-4o-realtime-preview">GPT-4o Realtime (Latest)</option>
                                <option value="gpt-4o-realtime-preview-2025-06-03">GPT-4o Realtime (2025-06-03)</option>
                                <option value="gpt-4o-realtime-preview-2024-12-17">GPT-4o Realtime (2024-12-17)</option>
                                <option value="gpt-4o-realtime-preview-2024-10-01">GPT-4o Realtime (2024-10-01)</option>
                            </optgroup>
                            <optgroup label="GPT-4o Mini Realtime">
                                <option value="gpt-4o-mini-realtime-preview">GPT-4o Mini Realtime (Latest)</option>
                                <option value="gpt-4o-mini-realtime-preview-2024-12-17">GPT-4o Mini Realtime (2024-12-17)</option>
                            </optgroup>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            Multilingual speech-to-speech. Supports EN, ES, FR, DE, IT, PT, JA, KO, ZH, AR +more.
                            <a href="https://platform.openai.com/docs/guides/realtime-conversations" target="_blank" rel="noopener noreferrer" className="ml-1 text-blue-500 hover:underline">API Docs ↗</a>
                        </p>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Voice</label>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.voice || 'alloy'}
                            onChange={(e) => handleChange('voice', e.target.value)}
                        >
                            <optgroup label="Original Voices">
                                <option value="alloy">Alloy</option>
                                <option value="echo">Echo</option>
                                <option value="shimmer">Shimmer</option>
                            </optgroup>
                            <optgroup label="New Voices (Oct 2024)">
                                <option value="ash">Ash</option>
                                <option value="ballad">Ballad</option>
                                <option value="coral">Coral</option>
                                <option value="sage">Sage</option>
                                <option value="verse">Verse</option>
                            </optgroup>
                        </select>
                        <p className="text-xs text-muted-foreground">
                            All voices support multilingual output. New voices (Ash, Ballad, etc.) added Oct 2024.
                            <a href="https://platform.openai.com/docs/api-reference/audio" target="_blank" rel="noopener noreferrer" className="ml-1 text-blue-500 hover:underline">Voice Docs ↗</a>
                        </p>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Temperature</label>
                        <input
                            type="number"
                            step="0.1"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.temperature || 0.8}
                            onChange={(e) => handleChange('temperature', parseFloat(e.target.value))}
                        />
                        <p className="text-xs text-muted-foreground">
                            Controls randomness (0.0-2.0). Lower = more focused, higher = more creative.
                        </p>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Max Response Tokens</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.max_response_output_tokens || 4096}
                            onChange={(e) => handleChange('max_response_output_tokens', parseInt(e.target.value))}
                        />
                        <p className="text-xs text-muted-foreground">
                            Maximum tokens in response. Higher allows longer answers but increases latency.
                        </p>
                    </div>
                </div>

                <div className="space-y-2">
                    <label className="text-sm font-medium">System Instructions</label>
                    <textarea
                        className="w-full p-2 rounded border border-input bg-background min-h-[100px] font-mono text-sm"
                        value={config.instructions || ''}
                        onChange={(e) => handleChange('instructions', e.target.value)}
                        placeholder="You are a helpful assistant..."
                    />
                </div>

                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">Turn Detection (VAD)</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Type</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.turn_detection?.type || 'server_vad'}
                                onChange={(e) => handleNestedChange('turn_detection', 'type', e.target.value)}
                            >
                                <option value="server_vad">Server VAD</option>
                                <option value="none">None (Push to Talk)</option>
                            </select>
                            <p className="text-xs text-muted-foreground">
                                Server VAD auto-detects speech turns. None requires manual control.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Threshold (0.0 - 1.0)</label>
                            <input
                                type="number"
                                step="0.1"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.turn_detection?.threshold || 0.6}
                                onChange={(e) => handleNestedChange('turn_detection', 'threshold', parseFloat(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Voice activity sensitivity. Lower = more sensitive to speech.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Silence Duration (ms)</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.turn_detection?.silence_duration_ms || 1000}
                                onChange={(e) => handleNestedChange('turn_detection', 'silence_duration_ms', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Silence needed to end a turn. Higher = waits longer for user to finish.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Prefix Padding (ms)</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.turn_detection?.prefix_padding_ms || 300}
                                onChange={(e) => handleNestedChange('turn_detection', 'prefix_padding_ms', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Audio included before detected speech start. Prevents clipping.
                            </p>
                        </div>
                        <div className="flex items-center space-x-2">
                            <input
                                type="checkbox"
                                id="create_response"
                                className="rounded border-input"
                                checked={config.turn_detection?.create_response ?? true}
                                onChange={(e) => handleNestedChange('turn_detection', 'create_response', e.target.checked)}
                            />
                            <label htmlFor="create_response" className="text-sm font-medium">Create Response</label>
                        </div>
                    </div>
                </div>

            <div className="space-y-4">
                <h4 className="font-semibold text-sm border-b pb-2">Audio Configuration</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Input Encoding</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.input_encoding || 'ulaw'}
                                onChange={(e) => handleChange('input_encoding', e.target.value)}
                            >
                                <option value="ulaw">μ-law</option>
                                <option value="pcm16">PCM16</option>
                                <option value="linear16">Linear16</option>
                            </select>
                            <p className="text-xs text-muted-foreground">
                                Audio format from Asterisk. Use μ-law for standard telephony.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Input Sample Rate (Hz)</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.input_sample_rate_hz || 8000}
                                onChange={(e) => handleChange('input_sample_rate_hz', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Sample rate from Asterisk. Standard telephony uses 8000 Hz.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Output Encoding</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.output_encoding || 'linear16'}
                                onChange={(e) => handleChange('output_encoding', e.target.value)}
                            >
                                <option value="linear16">Linear16</option>
                                <option value="pcm16">PCM16</option>
                                <option value="ulaw">μ-law</option>
                            </select>
                            <p className="text-xs text-muted-foreground">
                                Audio format from OpenAI. Linear16 provides best quality.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Output Sample Rate (Hz)</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.output_sample_rate_hz || 24000}
                                onChange={(e) => handleChange('output_sample_rate_hz', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Sample rate from OpenAI. 24000 Hz is native for Realtime API.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Target Encoding</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.target_encoding || 'mulaw'}
                                onChange={(e) => handleChange('target_encoding', e.target.value)}
                            >
                                <option value="mulaw">μ-law</option>
                                <option value="ulaw">ulaw</option>
                                <option value="pcm16">PCM16</option>
                                <option value="linear16">Linear16</option>
                            </select>
                            <p className="text-xs text-muted-foreground">
                                Final format for playback to caller. Match your Asterisk codec.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Target Sample Rate (Hz)</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.target_sample_rate_hz || 8000}
                                onChange={(e) => handleChange('target_sample_rate_hz', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Final sample rate for playback. 8000 Hz for standard telephony.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Provider Input Encoding</label>
                            <select
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.provider_input_encoding || 'linear16'}
                                onChange={(e) => handleChange('provider_input_encoding', e.target.value)}
                            >
                                <option value="linear16">Linear16</option>
                                <option value="pcm16">PCM16</option>
                            </select>
                            <p className="text-xs text-muted-foreground">
                                Format sent to OpenAI. Linear16 required for Realtime API.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Provider Input Sample Rate (Hz)</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.provider_input_sample_rate_hz || 24000}
                                onChange={(e) => handleChange('provider_input_sample_rate_hz', parseInt(e.target.value))}
                            />
                            <p className="text-xs text-muted-foreground">
                                Sample rate for OpenAI input. 24000 Hz required for Realtime API.
                            </p>
                        </div>
                    </div>
                </div>

                <div className="space-y-4">
                    <h4 className="font-semibold text-sm border-b pb-2">Advanced Settings</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Greeting</label>
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.greeting || ''}
                                onChange={(e) => handleChange('greeting', e.target.value)}
                                placeholder="Hello, how can I help you?"
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Input Gain Max (dB)</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.input_gain_max_db || 0}
                                onChange={(e) => handleChange('input_gain_max_db', parseFloat(e.target.value))}
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Input Gain Target RMS</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.input_gain_target_rms || 0}
                                onChange={(e) => handleChange('input_gain_target_rms', parseFloat(e.target.value))}
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Egress Pacer Warmup (ms)</label>
                            <input
                                type="number"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={config.egress_pacer_warmup_ms || 320}
                                onChange={(e) => handleChange('egress_pacer_warmup_ms', parseInt(e.target.value))}
                            />
                        </div>
                        <div className="flex items-center space-x-2">
                            <input
                                type="checkbox"
                                id="egress_pacer_enabled"
                                className="rounded border-input"
                                checked={config.egress_pacer_enabled ?? true}
                                onChange={(e) => handleChange('egress_pacer_enabled', e.target.checked)}
                            />
                            <label htmlFor="egress_pacer_enabled" className="text-sm font-medium">Enable Egress Pacer</label>
                        </div>
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
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Input Sample Rate (Hz)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.input_sample_rate_hz || 8000}
                            onChange={(e) => handleChange('input_sample_rate_hz', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Output Encoding</label>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.output_encoding || 'linear16'}
                            onChange={(e) => handleChange('output_encoding', e.target.value)}
                        >
                            <option value="linear16">Linear16</option>
                            <option value="pcm16">PCM16</option>
                        </select>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Output Sample Rate (Hz)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.output_sample_rate_hz || 24000}
                            onChange={(e) => handleChange('output_sample_rate_hz', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Target Encoding</label>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.target_encoding || 'mulaw'}
                            onChange={(e) => handleChange('target_encoding', e.target.value)}
                        >
                            <option value="mulaw">μ-law</option>
                            <option value="alaw">A-law</option>
                            <option value="linear16">Linear16</option>
                        </select>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Target Sample Rate (Hz)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.target_sample_rate_hz || 8000}
                            onChange={(e) => handleChange('target_sample_rate_hz', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Provider Input Encoding</label>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.provider_input_encoding || 'linear16'}
                            onChange={(e) => handleChange('provider_input_encoding', e.target.value)}
                        >
                            <option value="linear16">Linear16</option>
                            <option value="pcm16">PCM16</option>
                        </select>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Provider Input Sample Rate (Hz)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.provider_input_sample_rate_hz || 24000}
                            onChange={(e) => handleChange('provider_input_sample_rate_hz', parseInt(e.target.value))}
                        />
                    </div>
                </div>
            </div>

            <div className="space-y-4">
                <h4 className="font-semibold text-sm border-b pb-2">Behavior</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="flex items-center space-x-2">
                        <input
                            type="checkbox"
                            id="egress_pacer_enabled"
                            className="rounded border-input"
                            checked={config.egress_pacer_enabled ?? true}
                            onChange={(e) => handleChange('egress_pacer_enabled', e.target.checked)}
                        />
                        <label htmlFor="egress_pacer_enabled" className="text-sm font-medium">Egress Pacer</label>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Egress Pacer Warmup (ms)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.egress_pacer_warmup_ms || 320}
                            onChange={(e) => handleChange('egress_pacer_warmup_ms', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Response Modalities</label>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.response_modalities || 'audio,text'}
                            onChange={(e) => handleChange('response_modalities', e.target.value)}
                        >
                            <option value="audio">Audio</option>
                            <option value="audio,text">Audio & Text</option>
                            <option value="text">Text</option>
                        </select>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Greeting</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.greeting || ''}
                            onChange={(e) => handleChange('greeting', e.target.value)}
                            placeholder="Hello, how can I help you?"
                        />
                    </div>
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
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Input Gain Target RMS</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.input_gain_target_rms || 0}
                            onChange={(e) => handleChange('input_gain_target_rms', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Input Gain Max dB</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.input_gain_max_db || 0}
                            onChange={(e) => handleChange('input_gain_max_db', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Farewell Hangup Delay (seconds)</label>
                        <input
                            type="number"
                            step="0.5"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.farewell_hangup_delay_sec ?? ''}
                            onChange={(e) => handleChange('farewell_hangup_delay_sec', e.target.value ? parseFloat(e.target.value) : null)}
                            placeholder="Use global default (2.5s)"
                        />
                        <p className="text-xs text-muted-foreground">
                            Seconds to wait after farewell audio before hanging up. Leave empty to use global default.
                        </p>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default OpenAIRealtimeProviderForm;
