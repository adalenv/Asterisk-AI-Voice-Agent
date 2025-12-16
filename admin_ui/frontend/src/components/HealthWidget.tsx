import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Activity, CheckCircle2, Cpu, RefreshCw, Settings, Terminal, XCircle, HardDrive, AlertCircle, Layers, Box, Play, Star } from 'lucide-react';
import yaml from 'js-yaml';
import { ConfigCard } from './ui/ConfigCard';
import axios from 'axios';

interface HealthInfo {
    local_ai_server: {
        status: string;
        details: any;
    };
    ai_engine: {
        status: string;
        details: any;
    };
}

interface ModelInfo {
    name: string;
    path: string;
    type: string;
    backend?: string;
    size_mb?: number;
}

interface AvailableModels {
    stt: Record<string, ModelInfo[]>;
    tts: Record<string, ModelInfo[]>;
    llm: ModelInfo[];
}

interface PendingChanges {
    stt?: { backend: string; modelPath?: string; embedded?: boolean };
    tts?: { backend: string; modelPath?: string; voice?: string; mode?: string };
    llm?: { modelPath: string };
}

interface BackendCapabilities {
    stt: {
        vosk: { available: boolean; reason: string };
        sherpa: { available: boolean; reason: string };
        kroko_embedded: { available: boolean; reason: string };
        kroko_cloud: { available: boolean; reason: string };
    };
    tts: {
        piper: { available: boolean; reason: string };
        kokoro: { available: boolean; reason: string };
    };
    llm: { available: boolean; reason: string };
}

export const HealthWidget = () => {
    const [health, setHealth] = useState<HealthInfo | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [availableModels, setAvailableModels] = useState<AvailableModels | null>(null);
    const [capabilities, setCapabilities] = useState<BackendCapabilities | null>(null);
    const [restarting, setRestarting] = useState(false);
    const [pendingChanges, setPendingChanges] = useState<PendingChanges>({});
    const [applyingChanges, setApplyingChanges] = useState(false);
    const [startingLocalAI, setStartingLocalAI] = useState(false);
    const [startingAIEngine, setStartingAIEngine] = useState(false);
    const [defaultProvider, setDefaultProvider] = useState<string | null>(null);
    const [activePipeline, setActivePipeline] = useState<string | null>(null);

    const handleStartContainer = async (containerName: string, setStarting: (v: boolean) => void) => {
        setStarting(true);
        try {
            await axios.post(`/api/system/containers/${containerName}/start`);
            // Wait a bit for container to start
            setTimeout(() => {
                setStarting(false);
            }, 5000);
        } catch (err: any) {
            console.error('Failed to start container', err);
            alert(`Failed to start ${containerName}: ${err.response?.data?.detail || err.message}`);
            setStarting(false);
        }
    };

    useEffect(() => {
        const fetchHealth = async () => {
            try {
                const res = await axios.get('/api/system/health');
                setHealth(res.data);
                setError(null);
            } catch (err) {
                console.error('Failed to fetch health', err);
                setError('Failed to load health status');
            } finally {
                setLoading(false);
            }
        };
        fetchHealth();
        // Refresh every 5 seconds
        const interval = setInterval(fetchHealth, 5000);
        return () => clearInterval(interval);
    }, []);

    // Fetch available models and capabilities
    useEffect(() => {
        const fetchModels = async () => {
            try {
                const res = await axios.get('/api/local-ai/models');
                setAvailableModels(res.data);
            } catch (err) {
                console.error('Failed to fetch available models', err);
            }
        };
        const fetchCapabilities = async () => {
            try {
                const res = await axios.get('/api/local-ai/capabilities');
                setCapabilities(res.data);
            } catch (err) {
                console.error('Failed to fetch capabilities', err);
            }
        };
        fetchModels();
        fetchCapabilities();
    }, []);

    // Fetch default provider and active pipeline from config
    useEffect(() => {
        const fetchConfig = async () => {
            try {
                const res = await axios.get('/api/config/yaml');
                const parsed = yaml.load(res.data.content) as any;
                setDefaultProvider(parsed?.default_provider || null);
                setActivePipeline(parsed?.active_pipeline || null);
            } catch (err) {
                console.error('Failed to fetch config', err);
            }
        };
        fetchConfig();
        // Refresh every 10 seconds
        const interval = setInterval(fetchConfig, 10000);
        return () => clearInterval(interval);
    }, []);

    // Queue a model change (doesn't apply until user confirms)
    const queueChange = (modelType: 'stt' | 'tts' | 'llm', change: any) => {
        setPendingChanges(prev => ({
            ...prev,
            [modelType]: change
        }));
    };

    // Check if there are pending changes
    const hasPendingChanges = Object.keys(pendingChanges).length > 0;

    // Get the displayed value (pending or current) - returns backend:path format for model selection
    const getDisplayedBackend = (modelType: 'stt' | 'tts') => {
        if (modelType === 'stt') {
            if (pendingChanges.stt?.backend) {
                if (pendingChanges.stt.backend === 'kroko') {
                    return pendingChanges.stt.embedded ? 'kroko_embedded' : 'kroko_cloud';
                }
                // Return backend:path format for specific model
                if (pendingChanges.stt.modelPath) {
                    return `${pendingChanges.stt.backend}:${pendingChanges.stt.modelPath}`;
                }
                return pendingChanges.stt.backend;
            }
            const currentBackend = health?.local_ai_server.details.models?.stt?.backend || health?.local_ai_server.details.stt_backend || 'vosk';
            const currentPath = health?.local_ai_server.details.models?.stt?.path;
            if (currentBackend === 'kroko') {
                // kroko.embedded is nested in status response
                const krokoEmbedded = health?.local_ai_server.details.kroko?.embedded ?? health?.local_ai_server.details.kroko_embedded;
                return krokoEmbedded ? 'kroko_embedded' : 'kroko_cloud';
            }
            // Return backend:path format to match selected model
            if (currentPath) {
                return `${currentBackend}:${currentPath}`;
            }
            return currentBackend;
        } else {
            // TTS
            if (pendingChanges.tts?.backend) {
                if (pendingChanges.tts.backend === 'kokoro') {
                    return pendingChanges.tts.mode === 'local' ? 'kokoro_local' : 'kokoro_cloud';
                }
                // Return backend:path format for specific model
                if (pendingChanges.tts.modelPath) {
                    return `${pendingChanges.tts.backend}:${pendingChanges.tts.modelPath}`;
                }
                return pendingChanges.tts.backend;
            }
            const currentBackend = health?.local_ai_server.details.models?.tts?.backend || health?.local_ai_server.details.tts_backend || 'piper';
            const currentPath = health?.local_ai_server.details.models?.tts?.path;
            if (currentBackend === 'kokoro') {
                // kokoro.mode is nested in status response
                const kokoroMode = health?.local_ai_server.details.kokoro?.mode || health?.local_ai_server.details.kokoro_mode || 'local';
                return kokoroMode === 'local' || kokoroMode === 'hf' ? 'kokoro_local' : 'kokoro_cloud';
            }
            // Return backend:path format to match selected model
            if (currentPath) {
                return `${currentBackend}:${currentPath}`;
            }
            return currentBackend;
        }
    };

    const getDisplayedLlmPath = () => {
        if (pendingChanges.llm?.modelPath) {
            return pendingChanges.llm.modelPath;
        }
        return health?.local_ai_server.details.models?.llm?.path || '';
    };

    // Apply all pending changes and restart
    const applyChanges = async () => {
        if (!hasPendingChanges) return;

        setApplyingChanges(true);
        setRestarting(true);

        try {
            // Apply each pending change (last one triggers the restart)
            const changes = Object.entries(pendingChanges);

            for (let i = 0; i < changes.length; i++) {
                const [modelType, change] = changes[i];
                const isLast = i === changes.length - 1;

                if (modelType === 'stt' || modelType === 'tts') {
                    const payload: any = {
                        model_type: modelType,
                        backend: change.backend,
                        model_path: change.modelPath,
                        voice: change.voice
                    };

                    // Add mode params if applicable
                    if (modelType === 'stt' && change.backend === 'kroko') {
                        payload.kroko_embedded = change.embedded;
                    }
                    if (modelType === 'tts' && change.backend === 'kokoro') {
                        payload.kokoro_mode = change.mode;
                    }

                    const res = await axios.post('/api/local-ai/switch', payload);

                    // Only check success on last change (which triggers restart)
                    if (isLast && !res.data.success) {
                        throw new Error(res.data.message || 'Failed to switch model');
                    }
                } else if (modelType === 'llm') {
                    const res = await axios.post('/api/local-ai/switch', {
                        model_type: 'llm',
                        backend: '',
                        model_path: change.modelPath
                    });

                    if (isLast && !res.data.success) {
                        throw new Error(res.data.message || 'Failed to switch model');
                    }
                }
            }

            // Clear pending changes
            setPendingChanges({});

            // Wait for the switch API to complete (it handles restart internally)
            // Add extra buffer time for model loading (can take up to 3 minutes)
            setTimeout(() => {
                setRestarting(false);
                setApplyingChanges(false);
            }, 30000);  // 30 seconds UI timeout (API handles the actual wait)
        } catch (err: any) {
            console.error('Failed to apply changes', err);
            alert(err.message || 'Failed to apply changes');
            setApplyingChanges(false);
            setRestarting(false);
        }
    };

    // Cancel pending changes
    const cancelChanges = () => {
        setPendingChanges({});
    };


    if (loading) return <div className="animate-pulse h-48 bg-muted rounded-lg mb-6"></div>;

    if (error) {
        return (
            <div className="bg-destructive/10 border border-destructive/20 text-destructive p-4 rounded-md mb-6 flex items-center">
                <AlertCircle className="w-5 h-5 mr-2" />
                {error}
            </div>
        );
    }

    if (!health) return null;

    const renderStatus = (status: string) => {
        if (status === 'connected') return <span className="text-green-500 font-medium flex items-center gap-1"><CheckCircle2 className="w-4 h-4" /> Connected</span>;
        if (status === 'degraded') return <span className="text-yellow-500 font-medium flex items-center gap-1"><Activity className="w-4 h-4" /> Degraded</span>;
        return <span className="text-red-500 font-medium flex items-center gap-1"><XCircle className="w-4 h-4" /> Error</span>;
    };

    const getModelName = (path: string) => {
        if (!path) return 'Unknown';
        const parts = path.split('/');
        return parts[parts.length - 1];
    };

    const getModelDisplay = (model: any) => {
        if (!model) return 'Not configured';
        if (model.display) return model.display;
        if (model.path) return getModelName(String(model.path));
        return 'Not configured';
    };



    return (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
            {/* Local AI Server Card */}
            <ConfigCard className="p-6">
                <div className="flex justify-between items-start mb-6">
                    <div className="flex items-center gap-4">
                        <div className="p-3 bg-blue-500/10 rounded-xl">
                            <Cpu className="w-6 h-6 text-blue-500" />
                        </div>
                        <div>
                            <h3 className="font-semibold text-lg">Local AI Server</h3>
                            <div className="mt-1">{renderStatus(health.local_ai_server.status)}</div>
                        </div>
                    </div>
                    <div className="flex gap-2">
                        <Link
                            to="/env"
                            className="p-2 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground transition-colors cursor-pointer inline-flex items-center justify-center"
                            title="Configure"
                        >
                            <Settings className="w-4 h-4" />
                        </Link>
                        <button
                            type="button"
                            onClick={async () => {
                                if (!window.confirm('Are you sure you want to restart the Local AI Server?')) return;
                                setRestarting(true);
                                try {
                                    await axios.post('/api/system/containers/local_ai_server/restart');
                                    // Poll for health
                                    setTimeout(() => setRestarting(false), 5000);
                                } catch (err) {
                                    console.error('Failed to restart', err);
                                    setRestarting(false);
                                }
                            }}
                            className="p-2 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                            title="Restart"
                        >
                            <RefreshCw className={`w-4 h-4 ${restarting ? 'animate-spin' : ''}`} />
                        </button>
                        <Link
                            to="/logs?container=local_ai_server"
                            className="p-2 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground transition-colors cursor-pointer inline-flex items-center justify-center"
                            title="View Logs"
                        >
                            <Terminal className="w-4 h-4" />
                        </Link>
                    </div>
                </div>

                {/* Start button when not connected */}
                {health.local_ai_server.status === 'error' && (
                    <div className="mt-4 p-4 bg-yellow-500/10 border border-yellow-500/30 rounded-lg">
                        <p className="text-sm text-yellow-600 dark:text-yellow-400 mb-3">
                            Local AI Server is not running. Start it to use local STT/TTS models.
                        </p>
                        <button
                            onClick={() => handleStartContainer('local_ai_server', setStartingLocalAI)}
                            disabled={startingLocalAI}
                            className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 disabled:opacity-50 transition-colors"
                        >
                            {startingLocalAI ? (
                                <>
                                    <RefreshCw className="w-4 h-4 animate-spin" />
                                    Starting...
                                </>
                            ) : (
                                <>
                                    <Play className="w-4 h-4" />
                                    Start Local AI Server
                                </>
                            )}
                        </button>
                    </div>
                )}

                {health.local_ai_server.status === 'connected' && (
                    <div className="space-y-4">
                        {/* STT Section */}
                        <div className="space-y-2">
                            <div className="flex justify-between items-center text-sm">
                                <span className="text-muted-foreground font-medium">STT</span>
                                <div className="flex items-center gap-2">
                                    {pendingChanges.stt && (
                                        <span className="px-2 py-1 rounded-md text-xs font-medium bg-yellow-500/10 text-yellow-500">
                                            Pending
                                        </span>
                                    )}
                                    <span className={`px-2 py-1 rounded-md text-xs font-medium ${health.local_ai_server.details.models?.stt?.loaded ? "bg-green-500/10 text-green-500" : "bg-yellow-500/10 text-yellow-500"}`}>
                                        {health.local_ai_server.details.models?.stt?.loaded ? "Loaded" : "Not Loaded"}
                                    </span>
                                </div>
                            </div>
                            <div className="flex gap-2">
                                <select
                                    className={`flex-1 text-xs p-2 rounded border bg-background ${pendingChanges.stt ? 'border-yellow-500' : 'border-border'}`}
                                    value={getDisplayedBackend('stt')}
                                    onChange={(e) => {
                                        const val = e.target.value;
                                        let backend = '';
                                        let modelPath = '';
                                        let embedded = false;

                                        if (val === 'kroko_embedded') {
                                            backend = 'kroko';
                                            embedded = true;
                                        } else if (val === 'kroko_cloud') {
                                            backend = 'kroko';
                                            embedded = false;
                                        } else if (val.includes(':')) {
                                            // Format: backend:path (e.g., "vosk:/app/models/stt/vosk-model-hi-0.22")
                                            const parts = val.split(':');
                                            backend = parts[0];
                                            modelPath = parts.slice(1).join(':'); // Handle paths with colons
                                        } else {
                                            backend = val;
                                        }

                                        const currentBackend = health?.local_ai_server.details.models?.stt?.backend || health?.local_ai_server.details.stt_backend;
                                        const currentPath = health?.local_ai_server.details.models?.stt?.path;
                                        const currentEmbedded = health?.local_ai_server.details.kroko?.embedded ?? health?.local_ai_server.details.kroko_embedded;

                                        // Check if changed
                                        const isBackendChanged = backend !== currentBackend;
                                        const isPathChanged = modelPath && modelPath !== currentPath;
                                        const isModeChanged = backend === 'kroko' && embedded !== currentEmbedded;

                                        if (isBackendChanged || isPathChanged || isModeChanged) {
                                            queueChange('stt', { backend, modelPath: modelPath || undefined, embedded });
                                        } else {
                                            setPendingChanges(prev => {
                                                const { stt, ...rest } = prev;
                                                return rest;
                                            });
                                        }
                                    }}
                                    disabled={applyingChanges}
                                >
                                    {availableModels?.stt && Object.entries(availableModels.stt).map(([backend, models]) => {
                                        if (backend === 'kroko') {
                                            // Only show Kroko options if available
                                            const krokoEmbeddedAvailable = capabilities?.stt?.kroko_embedded?.available;
                                            const krokoCloudAvailable = capabilities?.stt?.kroko_cloud?.available;
                                            if (!krokoEmbeddedAvailable && !krokoCloudAvailable) return null;
                                            return (
                                                <optgroup key="kroko" label="Kroko">
                                                    {krokoEmbeddedAvailable && (
                                                        <option key="kroko_embedded" value="kroko_embedded">Kroko (Embedded)</option>
                                                    )}
                                                    {krokoCloudAvailable && (
                                                        <option key="kroko_cloud" value="kroko_cloud">Kroko (Cloud API)</option>
                                                    )}
                                                </optgroup>
                                            );
                                        }
                                        if (backend === 'sherpa') {
                                            // Only show Sherpa if available and has models
                                            if (!capabilities?.stt?.sherpa?.available || models.length === 0) return null;
                                        }
                                        // Show individual models in optgroup by backend (only if models exist)
                                        return models.length > 0 && (
                                            <optgroup key={backend} label={backend.charAt(0).toUpperCase() + backend.slice(1)}>
                                                {models.map((model: any) => (
                                                    <option key={model.id || model.path} value={`${backend}:${model.path}`}>
                                                        {model.name}
                                                    </option>
                                                ))}
                                            </optgroup>
                                        );
                                    })}
                                </select>
                            </div>
                            <div className="text-xs text-muted-foreground bg-muted/50 p-2 rounded border border-border/50 truncate flex justify-between">
                                <span>{getModelDisplay(health.local_ai_server.details.models?.stt)}</span>
                                {health.local_ai_server.details.stt_backend === 'kroko' && (
                                    <span className="opacity-75">
                                        {(health.local_ai_server.details.kroko?.embedded ?? health.local_ai_server.details.kroko_embedded) ? `Embedded (Port ${health.local_ai_server.details.kroko?.port || health.local_ai_server.details.kroko_port || 6006})` : 'Cloud API'}
                                    </span>
                                )}
                            </div>
                            {/* Warning when Kroko embedded not available */}
                            {capabilities && !capabilities.stt?.kroko_embedded?.available && (
                                <div className="text-xs p-2 rounded bg-amber-500/10 border border-amber-500/20 text-amber-600 dark:text-amber-400 space-y-1">
                                    <div>
                                        <span className="font-medium">Kroko Embedded not available.</span>
                                        <span className="opacity-75"> First download a Kroko ONNX model from the </span>
                                        <Link to="/models" className="underline hover:text-amber-500">Models Page</Link>
                                        <span className="opacity-75">, then rebuild the container:</span>
                                    </div>
                                    <code className="block bg-black/20 dark:bg-white/10 px-2 py-1 rounded text-[10px] font-mono select-all">
                                        docker compose build --build-arg INCLUDE_KROKO_EMBEDDED=true local-ai-server && docker compose up -d local-ai-server
                                    </code>
                                </div>
                            )}
                        </div>

                        {/* LLM Section */}
                        <div className="space-y-2">
                            <div className="flex justify-between items-center text-sm">
                                <span className="text-muted-foreground font-medium">LLM</span>
                                <div className="flex items-center gap-2">
                                    {pendingChanges.llm && (
                                        <span className="px-2 py-1 rounded-md text-xs font-medium bg-yellow-500/10 text-yellow-500">
                                            Pending
                                        </span>
                                    )}
                                    <span className={`px-2 py-1 rounded-md text-xs font-medium ${health.local_ai_server.details.models?.llm?.loaded ? "bg-green-500/10 text-green-500" : "bg-yellow-500/10 text-yellow-500"}`}>
                                        {health.local_ai_server.details.models?.llm?.loaded ? "Loaded" : "Not Loaded"}
                                    </span>
                                </div>
                            </div>
                            <select
                                className={`w-full text-xs p-2 rounded border bg-background ${pendingChanges.llm ? 'border-yellow-500' : 'border-border'}`}
                                value={getDisplayedLlmPath()}
                                onChange={(e) => {
                                    const modelPath = e.target.value;
                                    const currentPath = health?.local_ai_server.details.models?.llm?.path;
                                    if (modelPath !== currentPath) {
                                        queueChange('llm', { modelPath });
                                    } else {
                                        setPendingChanges(prev => {
                                            const { llm, ...rest } = prev;
                                            return rest;
                                        });
                                    }
                                }}
                                disabled={applyingChanges}
                            >
                                {availableModels?.llm?.map((model) => (
                                    <option key={model.path} value={model.path}>
                                        {model.name} {model.size_mb ? `(${model.size_mb} MB)` : ''}
                                    </option>
                                ))}
                            </select>
                            <div className="text-xs text-muted-foreground bg-muted/50 p-2 rounded border border-border/50 truncate">
                                {getModelDisplay(health.local_ai_server.details.models?.llm)}
                            </div>
                        </div>

                        {/* TTS Section */}
                        <div className="space-y-2">
                            <div className="flex justify-between items-center text-sm">
                                <span className="text-muted-foreground font-medium">TTS</span>
                                <div className="flex items-center gap-2">
                                    {pendingChanges.tts && (
                                        <span className="px-2 py-1 rounded-md text-xs font-medium bg-yellow-500/10 text-yellow-500">
                                            Pending
                                        </span>
                                    )}
                                    <span className={`px-2 py-1 rounded-md text-xs font-medium ${health.local_ai_server.details.models?.tts?.loaded ? "bg-green-500/10 text-green-500" : "bg-yellow-500/10 text-yellow-500"}`}>
                                        {health.local_ai_server.details.models?.tts?.loaded ? "Loaded" : "Not Loaded"}
                                    </span>
                                </div>
                            </div>
                            <div className="flex gap-2">
                                <select
                                    className={`flex-1 text-xs p-2 rounded border bg-background ${pendingChanges.tts ? 'border-yellow-500' : 'border-border'}`}
                                    value={getDisplayedBackend('tts')}
                                    onChange={(e) => {
                                        const val = e.target.value;
                                        let backend = '';
                                        let modelPath = '';
                                        let mode = 'local';

                                        if (val === 'kokoro_local') {
                                            backend = 'kokoro';
                                            mode = 'local';
                                        } else if (val === 'kokoro_cloud') {
                                            backend = 'kokoro';
                                            mode = 'api';
                                        } else if (val.includes(':')) {
                                            // Format: backend:path (e.g., "piper:/app/models/tts/en_US-lessac-medium.onnx")
                                            const parts = val.split(':');
                                            backend = parts[0];
                                            modelPath = parts.slice(1).join(':');
                                        } else {
                                            backend = val;
                                        }

                                        const currentBackend = health?.local_ai_server.details.models?.tts?.backend || health?.local_ai_server.details.tts_backend;
                                        const currentPath = health?.local_ai_server.details.models?.tts?.path;
                                        const currentMode = health?.local_ai_server.details.kokoro?.mode || health?.local_ai_server.details.kokoro_mode;

                                        const isBackendChanged = backend !== currentBackend;
                                        const isPathChanged = modelPath && modelPath !== currentPath;
                                        const isModeChanged = backend === 'kokoro' && mode !== currentMode;

                                        if (isBackendChanged || isPathChanged || isModeChanged) {
                                            const change: any = { backend, modelPath: modelPath || undefined };
                                            if (backend === 'kokoro') {
                                                change.voice = 'af_heart';
                                                change.mode = mode;
                                            }
                                            queueChange('tts', change);
                                        } else {
                                            setPendingChanges(prev => {
                                                const { tts, ...rest } = prev;
                                                return rest;
                                            });
                                        }
                                    }}
                                    disabled={applyingChanges}
                                >
                                    {availableModels?.tts && Object.entries(availableModels.tts).map(([backend, models]) => {
                                        if (backend === 'kokoro') {
                                            // Only show Kokoro if available
                                            if (!capabilities?.tts?.kokoro?.available && models.length === 0) return null;
                                            const kokoroApiConfigured = !!health?.local_ai_server?.details?.kokoro?.api_key_set;
                                            return (
                                                <optgroup key="kokoro" label="Kokoro">
                                                    {(capabilities?.tts?.kokoro?.available || models.length > 0) && (
                                                        <option key="kokoro_local" value="kokoro_local">Kokoro (Local)</option>
                                                    )}
                                                    {kokoroApiConfigured && (
                                                        <option key="kokoro_cloud" value="kokoro_cloud">Kokoro (Cloud/API)</option>
                                                    )}
                                                </optgroup>
                                            );
                                        }
                                        // Show individual models in optgroup by backend (only if models exist)
                                        return models.length > 0 && (
                                            <optgroup key={backend} label={backend.charAt(0).toUpperCase() + backend.slice(1)}>
                                                {models.map((model: any) => (
                                                    <option key={model.id || model.path} value={`${backend}:${model.path}`}>
                                                        {model.name}
                                                    </option>
                                                ))}
                                            </optgroup>
                                        );
                                    })}
                                </select>
                            </div>
                            <div className="text-xs text-muted-foreground bg-muted/50 p-2 rounded border border-border/50 truncate flex justify-between">
                                <span>{getModelDisplay(health.local_ai_server.details.models?.tts)}</span>
                                {health.local_ai_server.details.tts_backend === 'kokoro' && health.local_ai_server.details.kokoro_voice && (
                                    <span className="opacity-75">Voice: {health.local_ai_server.details.kokoro_voice}</span>
                                )}
                            </div>
                            {!!capabilities?.tts?.kokoro?.available && !health?.local_ai_server?.details?.kokoro?.api_key_set && (
                                <div className="text-xs p-2 rounded bg-muted/30 border border-border/50 text-muted-foreground">
                                    Enable Kokoro Cloud/API by setting `KOKORO_API_KEY` (and `KOKORO_MODE=api`) in <Link to="/env" className="underline">Env</Link>.
                                </div>
                            )}
                            {health?.local_ai_server?.details?.models?.tts?.backend === 'kokoro' &&
                                !health?.local_ai_server?.details?.kokoro?.api_key_set &&
                                (health?.local_ai_server?.details?.kokoro?.mode || health?.local_ai_server?.details?.kokoro_mode) !== 'local' && (
                                    <div className="text-xs p-2 rounded bg-amber-500/10 border border-amber-500/20 text-amber-600 dark:text-amber-400">
                                        Kokoro Cloud/API requires `KOKORO_API_KEY`. Configure it in <Link to="/env" className="underline">Env</Link>.
                                    </div>
                                )}
                        </div>

                        {/* Apply Changes Banner */}
                        {(hasPendingChanges || restarting) && (
                            <div className={`border rounded-lg p-3 space-y-2 ${restarting ? 'bg-blue-500/10 border-blue-500/30' : 'bg-yellow-500/10 border-yellow-500/30'}`}>
                                <div className={`flex items-center gap-2 text-sm font-medium ${restarting ? 'text-blue-600 dark:text-blue-400' : 'text-yellow-600 dark:text-yellow-400'}`}>
                                    {restarting ? (
                                        <>
                                            <RefreshCw className="w-4 h-4 animate-spin" />
                                            Restarting Local AI Server...
                                        </>
                                    ) : (
                                        <>
                                            <AlertCircle className="w-4 h-4" />
                                            {Object.keys(pendingChanges).length} change(s) pending
                                        </>
                                    )}
                                </div>
                                {!restarting && (
                                    <div className="flex gap-2">
                                        <button
                                            onClick={applyChanges}
                                            disabled={applyingChanges}
                                            className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-green-600 text-white rounded text-sm font-medium hover:bg-green-700 disabled:opacity-50 transition-colors"
                                        >
                                            {applyingChanges ? (
                                                <>
                                                    <RefreshCw className="w-4 h-4 animate-spin" />
                                                    Applying...
                                                </>
                                            ) : (
                                                <>
                                                    <CheckCircle2 className="w-4 h-4" />
                                                    Apply & Restart
                                                </>
                                            )}
                                        </button>
                                        <button
                                            onClick={cancelChanges}
                                            disabled={applyingChanges}
                                            className="flex items-center gap-1 px-3 py-2 bg-muted text-muted-foreground rounded text-sm font-medium hover:bg-muted/80 disabled:opacity-50 transition-colors"
                                        >
                                            <XCircle className="w-4 h-4" />
                                            Cancel
                                        </button>
                                    </div>
                                )}
                                {restarting && (
                                    <div className="text-xs text-muted-foreground">
                                        Please wait, this may take 10-15 seconds...
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                )}
            </ConfigCard>

            {/* AI Engine Card */}
            <ConfigCard className="p-6">
                <div className="flex justify-between items-start mb-6">
                    <div className="flex items-center gap-4">
                        <div className="p-3 bg-purple-500/10 rounded-xl">
                            <HardDrive className="w-6 h-6 text-purple-500" />
                        </div>
                        <div>
                            <h3 className="font-semibold text-lg">AI Engine</h3>
                            <div className="mt-1">{renderStatus(health.ai_engine.status)}</div>
                        </div>
                    </div>
                </div>

                {/* Start button when not connected */}
                {health.ai_engine.status === 'error' && (
                    <div className="mt-4 p-4 bg-yellow-500/10 border border-yellow-500/30 rounded-lg">
                        <p className="text-sm text-yellow-600 dark:text-yellow-400 mb-3">
                            AI Engine is not running. Start it to handle voice calls.
                        </p>
                        <button
                            onClick={() => handleStartContainer('ai_engine', setStartingAIEngine)}
                            disabled={startingAIEngine}
                            className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 disabled:opacity-50 transition-colors"
                        >
                            {startingAIEngine ? (
                                <>
                                    <RefreshCw className="w-4 h-4 animate-spin" />
                                    Starting...
                                </>
                            ) : (
                                <>
                                    <Play className="w-4 h-4" />
                                    Start AI Engine
                                </>
                            )}
                        </button>
                    </div>
                )}

                {(health.ai_engine.status === 'connected' || health.ai_engine.status === 'degraded') && (
                    <div className="space-y-6">
                        {/* ARI Status */}
                        <div className="flex justify-between items-center p-3 bg-muted/30 rounded-lg border border-border/50">
                            <span className="text-sm font-medium text-muted-foreground">ARI Connection</span>
                            <span className={`flex items-center gap-1.5 text-sm font-medium ${health.ai_engine.details.ari_connected ? "text-green-500" : "text-red-500"}`}>
                                <span className={`w-2 h-2 rounded-full ${health.ai_engine.details.ari_connected ? "bg-green-500" : "bg-red-500"}`}></span>
                                {health.ai_engine.details.ari_connected ? "Connected" : "Disconnected"}
                            </span>
                        </div>

                        {/* Default Configuration */}
                        <div>
                            <div className="flex items-center gap-2 mb-3">
                                <Star className="w-4 h-4 text-muted-foreground" />
                                <h4 className="text-sm font-medium text-muted-foreground">Default Configuration</h4>
                            </div>
                            {activePipeline ? (
                                <div className="flex justify-between items-center text-sm p-2 rounded hover:bg-muted/50 transition-colors">
                                    <span>Active Pipeline</span>
                                    <span className="text-xs px-2 py-0.5 rounded-full bg-green-500/10 text-green-500">
                                        {activePipeline}
                                    </span>
                                </div>
                            ) : defaultProvider ? (
                                <div className="flex justify-between items-center text-sm p-2 rounded hover:bg-muted/50 transition-colors">
                                    <span className="capitalize">Default Provider</span>
                                    <span className="text-xs px-2 py-0.5 rounded-full bg-green-500/10 text-green-500 capitalize">
                                        {defaultProvider.replace('_', ' ')}
                                    </span>
                                </div>
                            ) : (
                                <div className="text-xs text-muted-foreground italic p-2">No default configured</div>
                            )}
                        </div>

                        {/* Pipelines */}
                        <div>
                            <div className="flex items-center gap-2 mb-3">
                                <Layers className="w-4 h-4 text-muted-foreground" />
                                <h4 className="text-sm font-medium text-muted-foreground">Active Pipelines</h4>
                            </div>
                            <div className="space-y-3">
                                {health.ai_engine.details.pipelines && Object.keys(health.ai_engine.details.pipelines).length > 0 ? (
                                    Object.entries(health.ai_engine.details.pipelines).map(([pipelineName, pipelineInfo]: [string, any]) => (
                                        <div key={pipelineName} className="bg-muted/30 rounded-lg border border-border/50 overflow-hidden">
                                            <div className="px-3 py-2 bg-muted/50 border-b border-border/50">
                                                <span className="text-sm font-medium font-mono">{pipelineName}</span>
                                            </div>
                                            <div className="p-2 space-y-1">
                                                {pipelineInfo?.stt && (
                                                    <div className="flex justify-between items-center text-xs px-2 py-1">
                                                        <span className="text-muted-foreground">STT</span>
                                                        <span className="font-mono bg-blue-500/10 text-blue-500 px-2 py-0.5 rounded">{pipelineInfo.stt}</span>
                                                    </div>
                                                )}
                                                {pipelineInfo?.llm && (
                                                    <div className="flex justify-between items-center text-xs px-2 py-1">
                                                        <span className="text-muted-foreground">LLM</span>
                                                        <span className="font-mono bg-purple-500/10 text-purple-500 px-2 py-0.5 rounded">{pipelineInfo.llm}</span>
                                                    </div>
                                                )}
                                                {pipelineInfo?.tts && (
                                                    <div className="flex justify-between items-center text-xs px-2 py-1">
                                                        <span className="text-muted-foreground">TTS</span>
                                                        <span className="font-mono bg-green-500/10 text-green-500 px-2 py-0.5 rounded">{pipelineInfo.tts}</span>
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    ))
                                ) : (
                                    <div className="text-xs text-muted-foreground italic">No pipelines configured</div>
                                )}
                            </div>
                        </div>

                        {/* Providers */}
                        <div>
                            <div className="flex items-center gap-2 mb-3">
                                <Box className="w-4 h-4 text-muted-foreground" />
                                <h4 className="text-sm font-medium text-muted-foreground">Providers</h4>
                            </div>
                            <div className="space-y-2">
                                {health.ai_engine.details.providers ? (
                                    Object.entries(health.ai_engine.details.providers).map(([name, info]: [string, any]) => (
                                        <div key={name} className="flex justify-between items-center text-sm p-2 rounded hover:bg-muted/50 transition-colors">
                                            <span className="capitalize">{name.replace('_', ' ')}</span>
                                            <span className={`text-xs px-2 py-0.5 rounded-full ${info.ready ? "bg-green-500/10 text-green-500" : "bg-red-500/10 text-red-500"}`}>
                                                {info.ready ? "Ready" : "Not Ready"}
                                            </span>
                                        </div>
                                    ))
                                ) : (
                                    <div className="text-xs text-muted-foreground italic">No providers loaded</div>
                                )}
                            </div>
                        </div>
                    </div>
                )}
            </ConfigCard>
        </div>
    );
};
