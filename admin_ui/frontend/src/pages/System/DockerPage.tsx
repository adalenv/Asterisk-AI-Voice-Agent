import { useState, useEffect } from 'react';
import { Container, RefreshCw, AlertCircle, Clock, CheckCircle2, XCircle } from 'lucide-react';
import { ConfigSection } from '../../components/ui/ConfigSection';
import { ConfigCard } from '../../components/ui/ConfigCard';
import axios from 'axios';

interface ContainerInfo {
    id: string;
    name: string;
    image: string;
    status: string;
    state: string;
    uptime?: string;
    started_at?: string;
    ports?: string[];
}

interface Toast {
    id: number;
    message: string;
    type: 'success' | 'error';
}

const DockerPage = () => {
    const [containers, setContainers] = useState<ContainerInfo[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [actionLoading, setActionLoading] = useState<string | null>(null);
    const [toasts, setToasts] = useState<Toast[]>([]);

    const showToast = (message: string, type: 'success' | 'error') => {
        const id = Date.now();
        setToasts(prev => [...prev, { id, message, type }]);
        setTimeout(() => {
            setToasts(prev => prev.filter(t => t.id !== id));
        }, 4000);
    };

    const fetchContainers = async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await axios.get('/api/system/containers');
            setContainers(res.data);
        } catch (err: any) {
            console.error('Failed to fetch containers', err);
            setError('Failed to load container status. Ensure Docker is running and the backend has access.');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchContainers();
    }, []);

    const handleRestart = async (id: string, name: string) => {
        setActionLoading(id);
        try {
            await axios.post(`/api/system/containers/${id}/restart`);
            showToast(`Container "${name}" restarted successfully`, 'success');
            await fetchContainers();
        } catch (err: any) {
            showToast('Failed to restart: ' + (err.response?.data?.detail || err.message), 'error');
        } finally {
            setActionLoading(null);
        }
    };

    const getStatusColor = (status: string) => {
        if (status.includes('running')) return 'bg-green-500/10 text-green-500 border-green-500/20';
        if (status.includes('exited')) return 'bg-red-500/10 text-red-500 border-red-500/20';
        return 'bg-gray-500/10 text-gray-500 border-gray-500/20';
    };

    return (
        <div className="space-y-6">
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Docker Containers</h1>
                    <p className="text-muted-foreground mt-1">
                        Monitor and manage the containerized services.
                    </p>
                </div>
                <button
                    onClick={fetchContainers}
                    disabled={loading}
                    className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
                >
                    <RefreshCw className={`w-4 h-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
                    Refresh
                </button>
            </div>

            {error && (
                <div className="bg-destructive/10 border border-destructive/20 text-destructive p-4 rounded-md flex items-center">
                    <AlertCircle className="w-5 h-5 mr-2" />
                    {error}
                </div>
            )}

            <ConfigSection title="Service Status" description="Current state of system containers.">
                <div className="grid gap-4">
                    {loading && containers.length === 0 ? (
                        <div className="text-center p-8 text-muted-foreground">Loading container status...</div>
                    ) : containers.length === 0 && !error ? (
                        <div className="text-center p-8 text-muted-foreground">No containers found.</div>
                    ) : (
                        containers.map(container => {
                            const containerName = container.name.replace(/^\//, '');
                            const isRestarting = actionLoading === containerName;
                            
                            return (
                                <ConfigCard key={container.id} className="p-4">
                                    <div className="flex items-center justify-between">
                                        <div className="flex items-center gap-4">
                                            <div className="p-3 bg-primary/10 rounded-lg">
                                                <Container className="w-6 h-6 text-primary" />
                                            </div>
                                            <div>
                                                <h4 className="font-semibold text-lg">{containerName}</h4>
                                                <p className="text-xs text-muted-foreground font-mono truncate max-w-[200px]" title={container.image}>
                                                    {container.image}
                                                </p>
                                            </div>
                                        </div>

                                        <div className="flex items-center gap-6">
                                            {/* Uptime */}
                                            {container.uptime && (
                                                <div className="text-right hidden sm:block">
                                                    <div className="flex items-center gap-1 text-xs text-muted-foreground">
                                                        <Clock className="w-3 h-3" />
                                                        <span>Uptime</span>
                                                    </div>
                                                    <div className="text-sm font-medium">{container.uptime}</div>
                                                </div>
                                            )}
                                            
                                            {/* Ports */}
                                            {container.ports && container.ports.length > 0 && (
                                                <div className="text-right hidden md:block">
                                                    <div className="text-xs text-muted-foreground">Ports</div>
                                                    <div className="text-xs font-mono">
                                                        {container.ports.slice(0, 2).join(', ')}
                                                        {container.ports.length > 2 && ` +${container.ports.length - 2}`}
                                                    </div>
                                                </div>
                                            )}

                                            {/* Status Badge */}
                                            <div className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${getStatusColor(container.status)}`}>
                                                {isRestarting ? 'RESTARTING' : container.status.toUpperCase()}
                                            </div>
                                            
                                            {/* Restart Button */}
                                            <button
                                                onClick={() => handleRestart(containerName, containerName)}
                                                disabled={isRestarting}
                                                className="p-2 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground disabled:opacity-50"
                                                title="Restart container"
                                            >
                                                <RefreshCw className={`w-4 h-4 ${isRestarting ? 'animate-spin' : ''}`} />
                                            </button>
                                        </div>
                                    </div>
                                </ConfigCard>
                            );
                        })
                    )}
                </div>
            </ConfigSection>

            {/* Toast Notifications */}
            <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
                {toasts.map(toast => (
                    <div
                        key={toast.id}
                        className={`flex items-center gap-2 px-4 py-3 rounded-lg shadow-lg text-sm font-medium animate-in slide-in-from-right ${
                            toast.type === 'success' 
                                ? 'bg-green-500 text-white' 
                                : 'bg-red-500 text-white'
                        }`}
                    >
                        {toast.type === 'success' ? (
                            <CheckCircle2 className="w-4 h-4" />
                        ) : (
                            <XCircle className="w-4 h-4" />
                        )}
                        {toast.message}
                    </div>
                ))}
            </div>
        </div>
    );
};

export default DockerPage;
