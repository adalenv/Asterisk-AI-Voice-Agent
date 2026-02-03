import { useState, useEffect } from 'react';
import { HardDrive, Download, Trash2, RefreshCw, CheckCircle2, XCircle, Loader2, Mic, Volume2, Brain, AlertTriangle, Server } from 'lucide-react';
import { ConfigSection } from '../../components/ui/ConfigSection';
import { ConfigCard } from '../../components/ui/ConfigCard';
import axios from 'axios';

interface ModelInfo {
    id: string;
    name: string;
    language?: string;
    region?: string;
    backend?: string;
    size_mb: number;
    size_display: string;
    model_path?: string;
    download_url?: string;
    config_url?: string;  // For TTS models that need JSON config
    voice_files?: Record<string, string>;  // For Kokoro TTS voice files
    installed?: boolean;
    quality?: string;
    gender?: string;
    auto_download?: boolean;  // Models that auto-download from HuggingFace on first use
    note?: string;  // Info note about the model
}

interface InstalledModel {
    name: string;
    path: string;
    size_mb: number;
    type: 'stt' | 'tts' | 'llm';
}

interface Toast {
    id: number;
    message: string;
    type: 'success' | 'error' | 'warning';
}

interface DownloadProgress {
    bytes_downloaded: number;
    total_bytes: number;
    percent: number;
    speed_bps: number;
    eta_seconds: number | null;
    current_file: string;
}

interface ActiveModels {
    stt: { backend: string; path: string; loaded: boolean };
    tts: { backend: string; path: string; loaded: boolean };
    llm: { path: string; loaded: boolean };
}

interface AvailableModels {
    stt: Record<string, { name: string; path: string }[]>;
    tts: Record<string, { name: string; path: string }[]>;
    llm: { name: string; path: string }[];
}

const ModelsPage = () => {
    const [catalog, setCatalog] = useState<{ stt: ModelInfo[]; tts: ModelInfo[]; llm: ModelInfo[] }>({ stt: [], tts: [], llm: [] });
    const [installedModels, setInstalledModels] = useState<InstalledModel[]>([]);
    const [languageNames, setLanguageNames] = useState<Record<string, string>>({});
    const [regionNames, setRegionNames] = useState<Record<string, string>>({});
    const [loading, setLoading] = useState(true);
    const [downloadingModel, setDownloadingModel] = useState<string | null>(null);
    const [downloadProgress, setDownloadProgress] = useState<DownloadProgress | null>(null);
    const [deletingModel, setDeletingModel] = useState<string | null>(null);
    const [selectedTab, setSelectedTab] = useState<'installed' | 'stt' | 'tts' | 'llm'>('installed');
    const [selectedRegion, setSelectedRegion] = useState<string>('all');
    const [toasts, setToasts] = useState<Toast[]>([]);
    
    // Active models state (from Local AI Server)
    const [activeModels, setActiveModels] = useState<ActiveModels | null>(null);
    const [availableModels, setAvailableModels] = useState<AvailableModels | null>(null);
    const [serverStatus, setServerStatus] = useState<'connected' | 'error' | 'loading'>('loading');
    const [restarting, setRestarting] = useState(false);
    const [pendingChanges, setPendingChanges] = useState<{ stt?: string; tts?: string; llm?: string }>({});

    const showToast = (message: string, type: 'success' | 'error' | 'warning') => {
        const id = Date.now();
        setToasts(prev => [...prev, { id, message, type }]);
        setTimeout(() => {
            setToasts(prev => prev.filter(t => t.id !== id));
        }, 4000);
    };

    const fetchModels = async () => {
        setLoading(true);
        try {
            // Fetch catalog
            const catalogRes = await axios.get('/api/wizard/local/available-models');
            if (catalogRes.data) {
                setCatalog(catalogRes.data.catalog);
                setLanguageNames(catalogRes.data.language_names || {});
                setRegionNames(catalogRes.data.region_names || {});
            }

            // Fetch installed models from local-ai-server
            const installedRes = await axios.get('/api/local-ai/models');
            if (installedRes.data) {
                // Flatten the nested response into a single array
                const models: InstalledModel[] = [];
                
                // Process STT models (grouped by backend)
                if (installedRes.data.stt) {
                    Object.entries(installedRes.data.stt).forEach(([backend, backendModels]: [string, any]) => {
                        if (Array.isArray(backendModels)) {
                            backendModels.forEach((m: any) => {
                                models.push({
                                    name: m.name,
                                    path: m.path,
                                    size_mb: m.size_mb || 0,
                                    type: 'stt'
                                });
                            });
                        }
                    });
                }
                
                // Process TTS models (grouped by backend)
                if (installedRes.data.tts) {
                    Object.entries(installedRes.data.tts).forEach(([backend, backendModels]: [string, any]) => {
                        if (Array.isArray(backendModels)) {
                            backendModels.forEach((m: any) => {
                                models.push({
                                    name: m.name,
                                    path: m.path,
                                    size_mb: m.size_mb || 0,
                                    type: 'tts'
                                });
                            });
                        }
                    });
                }
                
                // Process LLM models (flat array)
                if (Array.isArray(installedRes.data.llm)) {
                    installedRes.data.llm.forEach((m: any) => {
                        models.push({
                            name: m.name,
                            path: m.path,
                            size_mb: m.size_mb || 0,
                            type: 'llm'
                        });
                    });
                }
                
                setInstalledModels(models);
            }
        } catch (err) {
            console.error('Failed to fetch models', err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchModels();
        fetchActiveModels();
    }, []);

    // Fetch active models from Local AI Server health
    const fetchActiveModels = async () => {
        try {
            const healthRes = await axios.get('/api/system/health');
            const localAI = healthRes.data?.local_ai_server;
            if (localAI?.status === 'connected') {
                setServerStatus('connected');
                setActiveModels({
                    stt: {
                        backend: localAI.details?.models?.stt?.backend || 'unknown',
                        path: localAI.details?.models?.stt?.path || '',
                        loaded: localAI.details?.models?.stt?.loaded || false
                    },
                    tts: {
                        backend: localAI.details?.models?.tts?.backend || 'unknown',
                        path: localAI.details?.models?.tts?.path || '',
                        loaded: localAI.details?.models?.tts?.loaded || false
                    },
                    llm: {
                        path: localAI.details?.models?.llm?.path || '',
                        loaded: localAI.details?.models?.llm?.loaded || false
                    }
                });
            } else {
                setServerStatus('error');
            }
            
            // Also fetch available models for switching
            const modelsRes = await axios.get('/api/local-ai/models');
            if (modelsRes.data) {
                setAvailableModels(modelsRes.data);
            }
        } catch (err) {
            setServerStatus('error');
        }
    };

    // Handle model switch
    const handleModelSwitch = async (modelType: 'stt' | 'tts' | 'llm', backend: string, modelPath: string) => {
        setRestarting(true);
        try {
            await axios.post('/api/local-ai/switch', {
                model_type: modelType,
                backend: backend,
                model_path: modelPath
            });
            showToast(`Switching ${modelType.toUpperCase()} model... Server will restart.`, 'success');
            // Wait for restart then refresh
            setTimeout(() => {
                fetchActiveModels();
                setRestarting(false);
            }, 15000);
        } catch (err: any) {
            showToast(`Failed to switch model: ${err.response?.data?.detail || err.message}`, 'error');
            setRestarting(false);
        }
    };

    // Get model name from path
    const getModelName = (path: string) => {
        if (!path) return 'None';
        const parts = path.split('/');
        return parts[parts.length - 1] || path;
    };

    const handleDownload = async (model: ModelInfo, type: 'stt' | 'tts' | 'llm') => {
        if (!model.download_url) {
            showToast('This model requires an API key and cannot be downloaded', 'error');
            return;
        }

        setDownloadingModel(model.id);
        setDownloadProgress(null);
        try {
            const startRes = await axios.post('/api/wizard/local/download-model', {
                model_id: model.id,
                type: type,
                download_url: model.download_url,
                model_path: model.model_path,
                config_url: model.config_url,  // For TTS models (Piper JSON config)
                voice_files: model.voice_files  // For Kokoro TTS voice files
            });
            const jobId = startRes.data?.job_id;
            const diskWarning = startRes.data?.disk_warning;
            if (diskWarning) showToast(diskWarning, 'warning');
            showToast(`Started downloading ${model.name}`, 'success');
            // Poll for completion with progress updates
            const pollDownload = async () => {
                try {
                    const res = await axios.get('/api/wizard/local/download-progress', {
                        params: jobId ? { job_id: jobId } : undefined
                    });
                    // Update progress state - always set if running to show progress bar
                    if (res.data.running) {
                        setDownloadProgress({
                            bytes_downloaded: res.data.bytes_downloaded || 0,
                            total_bytes: res.data.total_bytes || 0,
                            percent: res.data.percent || 0,
                            speed_bps: res.data.speed_bps || 0,
                            eta_seconds: res.data.eta_seconds,
                            current_file: res.data.current_file || ''
                        });
                    }
                    
                    if (res.data.completed) {
                        showToast(`${model.name} downloaded successfully!`, 'success');
                        setDownloadingModel(null);
                        setDownloadProgress(null);
                        fetchModels();
                    } else if (res.data.error) {
                        showToast(`Download failed: ${res.data.error}`, 'error');
                        setDownloadingModel(null);
                        setDownloadProgress(null);
                    } else if (res.data.running) {
                        setTimeout(pollDownload, 1000);
                    } else {
                        setDownloadingModel(null);
                        setDownloadProgress(null);
                    }
                } catch (err) {
                    setTimeout(pollDownload, 2000);
                }
            };
            setTimeout(pollDownload, 500);
        } catch (err: any) {
            const message = err.response?.data?.detail || err.response?.data?.message || err.message || 'Unknown error';
            showToast(`Failed to start download: ${message}`, 'error');
            setDownloadingModel(null);
            setDownloadProgress(null);
        }
    };

    const handleDelete = async (model: InstalledModel) => {
        if (!confirm(`Are you sure you want to delete "${model.name}"? This cannot be undone.`)) {
            return;
        }

        setDeletingModel(model.name);
        try {
            await axios.delete('/api/local-ai/models', {
                data: { model_path: model.path, type: model.type }
            });
            showToast(`${model.name} deleted successfully`, 'success');
            fetchModels();
        } catch (err: any) {
            const message = err.response?.data?.detail || err.message || 'Unknown error';
            showToast(`Failed to delete model: ${message}`, 'error');
        } finally {
            setDeletingModel(null);
        }
    };

    const getTypeIcon = (type: string) => {
        switch (type) {
            case 'stt': return <Mic className="w-4 h-4" />;
            case 'tts': return <Volume2 className="w-4 h-4" />;
            case 'llm': return <Brain className="w-4 h-4" />;
            default: return <HardDrive className="w-4 h-4" />;
        }
    };

    const filterByRegion = (models: ModelInfo[]) => {
        if (selectedRegion === 'all') return models;
        return models.filter(m => m.region === selectedRegion);
    };

    const getUniqueRegions = () => {
        const regions = new Set<string>();
        [...catalog.stt, ...catalog.tts].forEach(m => {
            if (m.region) regions.add(m.region);
        });
        return Array.from(regions);
    };

    const isModelInstalled = (modelPath: string) => {
        return installedModels.some(m => m.path.includes(modelPath) || m.name === modelPath);
    };

    // Get friendly display name for installed model by matching against catalog
    const getModelDisplayName = (model: InstalledModel): string => {
        const allCatalogModels = [...catalog.stt, ...catalog.tts, ...catalog.llm];
        const catalogMatch = allCatalogModels.find(cm => 
            cm.model_path && (model.path.includes(cm.model_path) || model.name === cm.model_path)
        );
        return catalogMatch?.name || model.name;
    };

    return (
        <div className="p-6 space-y-6">
            {/* Toast notifications */}
            <div className="fixed top-4 right-4 z-50 space-y-2">
                {toasts.map(toast => (
                    <div
                        key={toast.id}
                        className={`px-4 py-3 rounded-lg shadow-lg flex items-center gap-2 ${
                            toast.type === 'success' 
                                ? 'bg-green-600 text-white' 
                                : toast.type === 'warning'
                                    ? 'bg-yellow-600 text-white'
                                    : 'bg-red-600 text-white'
                        }`}
                    >
                        {toast.type === 'success' ? (
                            <CheckCircle2 className="w-4 h-4" />
                        ) : toast.type === 'warning' ? (
                            <AlertTriangle className="w-4 h-4" />
                        ) : (
                            <XCircle className="w-4 h-4" />
                        )}
                        {toast.message}
                    </div>
                ))}
            </div>

            {/* Active Models Section */}
            <ConfigCard className="mb-6">
                <div className="flex justify-between items-center mb-4">
                    <div className="flex items-center gap-3">
                        <div className="p-2 bg-green-500/10 rounded-lg">
                            <Server className="w-5 h-5 text-green-500" />
                        </div>
                        <div>
                            <h3 className="font-semibold">Active Models</h3>
                            <p className="text-xs text-muted-foreground">Currently loaded in Local AI Server</p>
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        <span className={`px-2 py-1 rounded text-xs font-medium ${
                            serverStatus === 'connected' ? 'bg-green-500/10 text-green-500' : 
                            serverStatus === 'error' ? 'bg-red-500/10 text-red-500' : 'bg-yellow-500/10 text-yellow-500'
                        }`}>
                            {serverStatus === 'connected' ? 'Connected' : serverStatus === 'error' ? 'Disconnected' : 'Loading...'}
                        </span>
                        <button
                            onClick={fetchActiveModels}
                            disabled={restarting}
                            className="p-2 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground transition-colors"
                            title="Refresh"
                        >
                            <RefreshCw className={`w-4 h-4 ${restarting ? 'animate-spin' : ''}`} />
                        </button>
                    </div>
                </div>

                {serverStatus === 'connected' && activeModels && (
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        {/* STT Model */}
                        <div className="p-4 rounded-lg border border-border bg-muted/30">
                            <div className="flex items-center gap-2 mb-2">
                                <Mic className="w-4 h-4 text-blue-500" />
                                <span className="text-sm font-medium">STT</span>
                                <span className={`ml-auto px-2 py-0.5 rounded text-xs ${
                                    activeModels.stt.loaded ? 'bg-green-500/10 text-green-500' : 'bg-yellow-500/10 text-yellow-500'
                                }`}>
                                    {activeModels.stt.loaded ? 'Loaded' : 'Not Loaded'}
                                </span>
                            </div>
                            <select 
                                className="w-full text-xs p-2 rounded border border-border bg-background"
                                value={pendingChanges.stt || `${activeModels.stt.backend}:${activeModels.stt.path}`}
                                onChange={(e) => {
                                    const val = e.target.value;
                                    setPendingChanges(prev => ({ ...prev, stt: val }));
                                }}
                                disabled={restarting}
                            >
                                {availableModels?.stt && Object.entries(availableModels.stt).map(([backend, models]) => (
                                    <optgroup key={backend} label={backend.charAt(0).toUpperCase() + backend.slice(1)}>
                                        {models.map((m: any) => (
                                            <option key={m.path} value={`${backend}:${m.path}`}>{m.name}</option>
                                        ))}
                                    </optgroup>
                                ))}
                            </select>
                            <div className="mt-2 text-xs text-muted-foreground truncate" title={activeModels.stt.path}>
                                {getModelName(activeModels.stt.path)}
                            </div>
                        </div>

                        {/* LLM Model */}
                        <div className="p-4 rounded-lg border border-border bg-muted/30">
                            <div className="flex items-center gap-2 mb-2">
                                <Brain className="w-4 h-4 text-purple-500" />
                                <span className="text-sm font-medium">LLM</span>
                                <span className={`ml-auto px-2 py-0.5 rounded text-xs ${
                                    activeModels.llm.loaded ? 'bg-green-500/10 text-green-500' : 'bg-yellow-500/10 text-yellow-500'
                                }`}>
                                    {activeModels.llm.loaded ? 'Loaded' : 'Not Loaded'}
                                </span>
                            </div>
                            <select 
                                className="w-full text-xs p-2 rounded border border-border bg-background"
                                value={pendingChanges.llm || activeModels.llm.path}
                                onChange={(e) => {
                                    setPendingChanges(prev => ({ ...prev, llm: e.target.value }));
                                }}
                                disabled={restarting}
                            >
                                {availableModels?.llm?.map((m: any) => (
                                    <option key={m.path} value={m.path}>{m.name}</option>
                                ))}
                            </select>
                            <div className="mt-2 text-xs text-muted-foreground truncate" title={activeModels.llm.path}>
                                {getModelName(activeModels.llm.path)}
                            </div>
                        </div>

                        {/* TTS Model */}
                        <div className="p-4 rounded-lg border border-border bg-muted/30">
                            <div className="flex items-center gap-2 mb-2">
                                <Volume2 className="w-4 h-4 text-green-500" />
                                <span className="text-sm font-medium">TTS</span>
                                <span className={`ml-auto px-2 py-0.5 rounded text-xs ${
                                    activeModels.tts.loaded ? 'bg-green-500/10 text-green-500' : 'bg-yellow-500/10 text-yellow-500'
                                }`}>
                                    {activeModels.tts.loaded ? 'Loaded' : 'Not Loaded'}
                                </span>
                            </div>
                            <select 
                                className="w-full text-xs p-2 rounded border border-border bg-background"
                                value={pendingChanges.tts || `${activeModels.tts.backend}:${activeModels.tts.path}`}
                                onChange={(e) => {
                                    const val = e.target.value;
                                    setPendingChanges(prev => ({ ...prev, tts: val }));
                                }}
                                disabled={restarting}
                            >
                                {availableModels?.tts && Object.entries(availableModels.tts).map(([backend, models]) => (
                                    <optgroup key={backend} label={backend.charAt(0).toUpperCase() + backend.slice(1)}>
                                        {models.map((m: any) => (
                                            <option key={m.path} value={`${backend}:${m.path}`}>{m.name}</option>
                                        ))}
                                    </optgroup>
                                ))}
                            </select>
                            <div className="mt-2 text-xs text-muted-foreground truncate" title={activeModels.tts.path}>
                                {getModelName(activeModels.tts.path)}
                            </div>
                        </div>
                    </div>
                )}

                {serverStatus === 'error' && (
                    <div className="p-4 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-600 dark:text-red-400">
                        Local AI Server is not connected. Start the server to manage models.
                    </div>
                )}

                {/* Apply Changes Button */}
                {Object.keys(pendingChanges).length > 0 && (
                    <div className="mt-4 flex gap-2">
                        <button
                            onClick={() => {
                                // Apply each pending change
                                Object.entries(pendingChanges).forEach(([type, value]) => {
                                    if (type === 'llm') {
                                        handleModelSwitch('llm', '', value);
                                    } else {
                                        const [backend, ...pathParts] = value.split(':');
                                        handleModelSwitch(type as 'stt' | 'tts', backend, pathParts.join(':'));
                                    }
                                });
                                setPendingChanges({});
                            }}
                            disabled={restarting}
                            className="flex-1 flex items-center justify-center gap-2 px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 disabled:opacity-50 transition-colors"
                        >
                            {restarting ? (
                                <>
                                    <RefreshCw className="w-4 h-4 animate-spin" />
                                    Restarting...
                                </>
                            ) : (
                                <>
                                    <CheckCircle2 className="w-4 h-4" />
                                    Apply Changes & Restart
                                </>
                            )}
                        </button>
                        <button
                            onClick={() => setPendingChanges({})}
                            disabled={restarting}
                            className="px-4 py-2 bg-muted text-muted-foreground rounded-md hover:bg-muted/80 transition-colors"
                        >
                            Cancel
                        </button>
                    </div>
                )}
            </ConfigCard>

            <ConfigSection
                title="Model Library"
                description="Download and manage STT, TTS, and LLM models"
            >
                {/* Header with tabs and refresh */}
                <div className="flex justify-between items-center mb-6">
                    <div className="flex gap-2">
                        <button
                            onClick={() => setSelectedTab('installed')}
                            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                                selectedTab === 'installed'
                                    ? 'bg-primary text-primary-foreground'
                                    : 'bg-muted hover:bg-muted/80'
                            }`}
                        >
                            Installed ({installedModels.length})
                        </button>
                        <button
                            onClick={() => setSelectedTab('stt')}
                            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors flex items-center gap-2 ${
                                selectedTab === 'stt'
                                    ? 'bg-primary text-primary-foreground'
                                    : 'bg-muted hover:bg-muted/80'
                            }`}
                        >
                            <Mic className="w-4 h-4" /> STT ({catalog.stt.length})
                        </button>
                        <button
                            onClick={() => setSelectedTab('tts')}
                            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors flex items-center gap-2 ${
                                selectedTab === 'tts'
                                    ? 'bg-primary text-primary-foreground'
                                    : 'bg-muted hover:bg-muted/80'
                            }`}
                        >
                            <Volume2 className="w-4 h-4" /> TTS ({catalog.tts.length})
                        </button>
                        <button
                            onClick={() => setSelectedTab('llm')}
                            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors flex items-center gap-2 ${
                                selectedTab === 'llm'
                                    ? 'bg-primary text-primary-foreground'
                                    : 'bg-muted hover:bg-muted/80'
                            }`}
                        >
                            <Brain className="w-4 h-4" /> LLM ({catalog.llm.length})
                        </button>
                    </div>
                    <div className="flex gap-2 items-center">
                        {selectedTab !== 'installed' && selectedTab !== 'llm' && (
                            <select
                                value={selectedRegion}
                                onChange={e => setSelectedRegion(e.target.value)}
                                className="px-3 py-2 rounded-md border border-input bg-background text-sm"
                            >
                                <option value="all">All Regions</option>
                                {getUniqueRegions().map(region => (
                                    <option key={region} value={region}>
                                        {regionNames[region] || region}
                                    </option>
                                ))}
                            </select>
                        )}
                        <button
                            onClick={fetchModels}
                            disabled={loading}
                            className="p-2 rounded-md bg-muted hover:bg-muted/80 transition-colors"
                        >
                            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                        </button>
                    </div>
                </div>

                {/* Download Progress Bar */}
                {downloadingModel && downloadProgress && (
                    <div className="mb-6 p-4 bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-200 dark:border-blue-800">
                        <div className="flex justify-between items-center mb-2">
                            <span className="text-sm font-medium text-blue-800 dark:text-blue-300">
                                Downloading: {downloadProgress.current_file || downloadingModel}
                            </span>
                            <span className="text-sm text-blue-600 dark:text-blue-400">
                                {downloadProgress.total_bytes > 0 ? `${downloadProgress.percent}%` : 'Downloading...'}
                            </span>
                        </div>
                        <div className="w-full bg-blue-200 dark:bg-blue-800 rounded-full h-2 mb-2 overflow-hidden">
                            {downloadProgress.total_bytes > 0 ? (
                                <div 
                                    className="bg-blue-600 dark:bg-blue-400 h-2 rounded-full transition-all duration-300"
                                    style={{ width: `${downloadProgress.percent}%` }}
                                />
                            ) : (
                                <div className="bg-blue-600 dark:bg-blue-400 h-2 rounded-full animate-pulse w-full opacity-50" />
                            )}
                        </div>
                        <div className="flex justify-between text-xs text-blue-600 dark:text-blue-400">
                            <span>
                                {(downloadProgress.bytes_downloaded / (1024 * 1024)).toFixed(1)} MB
                                {downloadProgress.total_bytes > 0 && ` / ${(downloadProgress.total_bytes / (1024 * 1024)).toFixed(1)} MB`}
                            </span>
                            <span>
                                {downloadProgress.speed_bps > 0 && `${(downloadProgress.speed_bps / (1024 * 1024)).toFixed(2)} MB/s`}
                                {downloadProgress.eta_seconds !== null && downloadProgress.eta_seconds > 0 && (
                                    <> • ETA: {Math.floor(downloadProgress.eta_seconds / 60)}m {downloadProgress.eta_seconds % 60}s</>
                                )}
                            </span>
                        </div>
                    </div>
                )}

                {loading ? (
                    <div className="flex justify-center items-center py-12">
                        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
                    </div>
                ) : (
                    <>
                        {/* Installed Models Tab */}
                        {selectedTab === 'installed' && (
                            <div className="space-y-4">
                                {installedModels.length === 0 ? (
                                    <div className="text-center py-12 text-muted-foreground">
                                        <HardDrive className="w-12 h-12 mx-auto mb-4 opacity-50" />
                                        <p>No models installed yet.</p>
                                        <p className="text-sm mt-2">Browse the STT, TTS, and LLM tabs to download models.</p>
                                    </div>
                                ) : (
                                    <div className="grid gap-4">
                                        {installedModels.map(model => (
                                            <ConfigCard key={model.path}>
                                                <div className="flex justify-between items-center">
                                                    <div className="flex items-center gap-3">
                                                        <div className={`p-2 rounded-lg ${
                                                            model.type === 'stt' ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-600' :
                                                            model.type === 'tts' ? 'bg-green-100 dark:bg-green-900/30 text-green-600' :
                                                            'bg-purple-100 dark:bg-purple-900/30 text-purple-600'
                                                        }`}>
                                                            {getTypeIcon(model.type)}
                                                        </div>
                                                        <div>
                                                            <p className="font-medium">{getModelDisplayName(model)}</p>
                                                            <p className="text-sm text-muted-foreground">
                                                                {model.type.toUpperCase()} • {model.size_mb.toFixed(0)} MB • {model.name}
                                                            </p>
                                                        </div>
                                                    </div>
                                                    <button
                                                        onClick={() => handleDelete(model)}
                                                        disabled={deletingModel === model.name}
                                                        className="p-2 rounded-md bg-red-100 dark:bg-red-900/30 text-red-600 hover:bg-red-200 dark:hover:bg-red-900/50 transition-colors"
                                                    >
                                                        {deletingModel === model.name ? (
                                                            <Loader2 className="w-4 h-4 animate-spin" />
                                                        ) : (
                                                            <Trash2 className="w-4 h-4" />
                                                        )}
                                                    </button>
                                                </div>
                                            </ConfigCard>
                                        ))}
                                    </div>
                                )}
                            </div>
                        )}

                        {/* STT Models Tab */}
                        {selectedTab === 'stt' && (
                            <div className="grid gap-4">
                                {filterByRegion(catalog.stt).map(model => (
                                    <ConfigCard key={model.id}>
                                        <div className="flex justify-between items-center">
                                            <div className="flex items-center gap-3">
                                                <div className="p-2 rounded-lg bg-blue-100 dark:bg-blue-900/30 text-blue-600">
                                                    <Mic className="w-4 h-4" />
                                                </div>
                                                <div>
                                                    <div className="flex items-center gap-2">
                                                        <p className="font-medium">{model.name}</p>
                                                        {isModelInstalled(model.model_path || '') && (
                                                            <span className="px-2 py-0.5 text-xs bg-green-100 dark:bg-green-900/30 text-green-600 rounded-full">
                                                                Installed
                                                            </span>
                                                        )}
                                                    </div>
                                                    <p className="text-sm text-muted-foreground">
                                                        {languageNames[model.language || ''] || model.language} • {model.size_display} • {model.backend}
                                                    </p>
                                                </div>
                                            </div>
                                            {!isModelInstalled(model.model_path || '') && model.download_url && (
                                                <button
                                                    onClick={() => handleDownload(model, 'stt')}
                                                    disabled={downloadingModel === model.id}
                                                    className="px-3 py-2 rounded-md bg-blue-600 text-white hover:bg-blue-700 transition-colors flex items-center gap-2 text-sm"
                                                >
                                                    {downloadingModel === model.id ? (
                                                        <Loader2 className="w-4 h-4 animate-spin" />
                                                    ) : (
                                                        <Download className="w-4 h-4" />
                                                    )}
                                                    Download
                                                </button>
                                            )}
                                            {!isModelInstalled(model.model_path || '') && model.auto_download && !model.download_url && (
                                                <div className="flex flex-col items-end gap-1">
                                                    <span className="px-3 py-2 rounded-md bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 text-sm flex items-center gap-2">
                                                        <RefreshCw className="w-4 h-4" />
                                                        Auto-download
                                                    </span>
                                                    <span className="text-[10px] text-amber-600 dark:text-amber-500 max-w-[200px] text-right">
                                                        {model.note || 'Downloads automatically when backend is enabled'}
                                                    </span>
                                                </div>
                                            )}
                                        </div>
                                    </ConfigCard>
                                ))}
                            </div>
                        )}

                        {/* TTS Models Tab */}
                        {selectedTab === 'tts' && (
                            <div className="grid gap-4">
                                {filterByRegion(catalog.tts).map(model => (
                                    <ConfigCard key={model.id}>
                                        <div className="flex justify-between items-center">
                                            <div className="flex items-center gap-3">
                                                <div className="p-2 rounded-lg bg-green-100 dark:bg-green-900/30 text-green-600">
                                                    <Volume2 className="w-4 h-4" />
                                                </div>
                                                <div>
                                                    <div className="flex items-center gap-2">
                                                        <p className="font-medium">{model.name}</p>
                                                        {model.gender && (
                                                            <span className="px-2 py-0.5 text-xs bg-muted text-muted-foreground rounded-full">
                                                                {model.gender}
                                                            </span>
                                                        )}
                                                        {isModelInstalled(model.model_path || '') && (
                                                            <span className="px-2 py-0.5 text-xs bg-green-100 dark:bg-green-900/30 text-green-600 rounded-full">
                                                                Installed
                                                            </span>
                                                        )}
                                                    </div>
                                                    <p className="text-sm text-muted-foreground">
                                                        {languageNames[model.language || ''] || model.language} • {model.size_display} • {model.quality || 'medium'}
                                                    </p>
                                                </div>
                                            </div>
                                            {!isModelInstalled(model.model_path || '') && model.download_url && (
                                                <button
                                                    onClick={() => handleDownload(model, 'tts')}
                                                    disabled={downloadingModel === model.id}
                                                    className="px-3 py-2 rounded-md bg-green-600 text-white hover:bg-green-700 transition-colors flex items-center gap-2 text-sm"
                                                >
                                                    {downloadingModel === model.id ? (
                                                        <Loader2 className="w-4 h-4 animate-spin" />
                                                    ) : (
                                                        <Download className="w-4 h-4" />
                                                    )}
                                                    Download
                                                </button>
                                            )}
                                            {!isModelInstalled(model.model_path || '') && model.auto_download && !model.download_url && (
                                                <div className="flex flex-col items-end gap-1">
                                                    <span className="px-3 py-2 rounded-md bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 text-sm flex items-center gap-2">
                                                        <RefreshCw className="w-4 h-4" />
                                                        Auto-download
                                                    </span>
                                                    <span className="text-[10px] text-amber-600 dark:text-amber-500 max-w-[200px] text-right">
                                                        {model.note || 'Downloads automatically when backend is enabled'}
                                                    </span>
                                                </div>
                                            )}
                                        </div>
                                    </ConfigCard>
                                ))}
                            </div>
                        )}

                        {/* LLM Models Tab */}
                        {selectedTab === 'llm' && (
                            <div className="grid gap-4">
                                {catalog.llm.map(model => (
                                    <ConfigCard key={model.id}>
                                        <div className="flex justify-between items-center">
                                            <div className="flex items-center gap-3">
                                                <div className="p-2 rounded-lg bg-purple-100 dark:bg-purple-900/30 text-purple-600">
                                                    <Brain className="w-4 h-4" />
                                                </div>
                                                <div>
                                                    <div className="flex items-center gap-2">
                                                        <p className="font-medium">{model.name}</p>
                                                        {isModelInstalled(model.model_path || '') && (
                                                            <span className="px-2 py-0.5 text-xs bg-green-100 dark:bg-green-900/30 text-green-600 rounded-full">
                                                                Installed
                                                            </span>
                                                        )}
                                                    </div>
                                                    <p className="text-sm text-muted-foreground">
                                                        {model.size_display}
                                                    </p>
                                                </div>
                                            </div>
                                            {!isModelInstalled(model.model_path || '') && model.download_url && (
                                                <button
                                                    onClick={() => handleDownload(model, 'llm')}
                                                    disabled={downloadingModel === model.id}
                                                    className="px-3 py-2 rounded-md bg-purple-600 text-white hover:bg-purple-700 transition-colors flex items-center gap-2 text-sm"
                                                >
                                                    {downloadingModel === model.id ? (
                                                        <Loader2 className="w-4 h-4 animate-spin" />
                                                    ) : (
                                                        <Download className="w-4 h-4" />
                                                    )}
                                                    Download
                                                </button>
                                            )}
                                        </div>
                                    </ConfigCard>
                                ))}
                            </div>
                        )}
                    </>
                )}
            </ConfigSection>
        </div>
    );
};

export default ModelsPage;
