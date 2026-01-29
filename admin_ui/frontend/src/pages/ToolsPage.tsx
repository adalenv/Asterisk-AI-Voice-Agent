import { useState, useEffect } from 'react';
import axios from 'axios';
import yaml from 'js-yaml';
import { Save, AlertCircle, RefreshCw, Loader2, Phone, Webhook, Search } from 'lucide-react';
import { YamlErrorBanner, YamlErrorInfo } from '../components/ui/YamlErrorBanner';
import { ConfigSection } from '../components/ui/ConfigSection';
import { ConfigCard } from '../components/ui/ConfigCard';
import ToolForm from '../components/config/ToolForm';
import HTTPToolForm from '../components/config/HTTPToolForm';
import { useAuth } from '../auth/AuthContext';
import { sanitizeConfigForSave } from '../utils/configSanitizers';

type ToolPhase = 'in_call' | 'pre_call' | 'post_call';

const ToolsPage = () => {
    const { token } = useAuth();
    const [config, setConfig] = useState<any>({});
    const [loading, setLoading] = useState(true);
    const [yamlError, setYamlError] = useState<YamlErrorInfo | null>(null);
    const [saving, setSaving] = useState(false);
    const [pendingRestart, setPendingRestart] = useState(false);
    const [restartingEngine, setRestartingEngine] = useState(false);
    const [activePhase, setActivePhase] = useState<ToolPhase>('in_call');

    useEffect(() => {
        fetchConfig();
    }, []);

    const fetchConfig = async () => {
        try {
            const res = await axios.get('/api/config/yaml');
            if (res.data.yaml_error) {
                setYamlError(res.data.yaml_error);
                setConfig({});
            } else {
                const parsed = yaml.load(res.data.content) as any;
                setConfig(parsed || {});
                setYamlError(null);
            }
        } catch (err) {
            console.error('Failed to load config', err);
            setYamlError(null);
        } finally {
            setLoading(false);
        }
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            const sanitized = sanitizeConfigForSave(config);
            await axios.post('/api/config/yaml', { content: yaml.dump(sanitized) }, {
                headers: { Authorization: `Bearer ${token}` },
                timeout: 30000  // 30 second timeout
            });
            setPendingRestart(true);
            alert('Tools configuration saved successfully');
        } catch (err: any) {
            console.error('Failed to save config', err);
            const detail = err.response?.data?.detail || err.message || 'Unknown error';
            alert(`Failed to save configuration: ${detail}`);
        } finally {
            setSaving(false);
        }
    };

    const handleRestartAIEngine = async (force: boolean = false) => {
        setRestartingEngine(true);
        try {
            const response = await axios.post(`/api/system/containers/ai_engine/restart?force=${force}`, {}, {
                headers: { Authorization: `Bearer ${token}` }
            });

            if (response.data.status === 'warning') {
                const confirmForce = window.confirm(
                    `${response.data.message}\n\nDo you want to force restart anyway? This may disconnect active calls.`
                );
                if (confirmForce) {
                    setRestartingEngine(false);
                    return handleRestartAIEngine(true);
                }
                return;
            }

            if (response.data.status === 'degraded') {
                alert(`AI Engine restarted but may not be fully healthy: ${response.data.output || 'Health check issue'}\n\nPlease verify manually.`);
                return;
            }

            setPendingRestart(false);
            alert('AI Engine restarted! Changes are now active.');
        } catch (error: any) {
            alert(`Failed to restart AI Engine: ${error.response?.data?.detail || error.message}`);
        } finally {
            setRestartingEngine(false);
        }
    };

    const updateToolsConfig = (newToolsConfig: any) => {
        // Extract root-level settings that should not be nested under tools
        const { farewell_hangup_delay_sec, ...toolsOnly } = newToolsConfig;
        
        // Update both tools config and root-level farewell_hangup_delay_sec
        const updatedConfig = { ...config, tools: toolsOnly };
        if (farewell_hangup_delay_sec !== undefined) {
            updatedConfig.farewell_hangup_delay_sec = farewell_hangup_delay_sec;
        }
        setConfig(updatedConfig);
    };

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading configuration...</div>;

    return (
        <div className="space-y-6">
            {yamlError && <YamlErrorBanner error={yamlError} />}
            <div className={`${pendingRestart ? 'bg-orange-500/15 border-orange-500/30' : 'bg-yellow-500/10 border-yellow-500/20'} border text-yellow-600 dark:text-yellow-500 p-4 rounded-md flex items-center justify-between`}>
                <div className="flex items-center">
                    <AlertCircle className="w-5 h-5 mr-2" />
                    Tool configuration changes require an AI Engine restart to take effect.
                </div>
                <button
                    onClick={() => handleRestartAIEngine(false)}
                    disabled={restartingEngine || !pendingRestart}
                    className={`flex items-center text-xs px-3 py-1.5 rounded transition-colors ${
                        pendingRestart
                            ? 'bg-orange-500 text-white hover:bg-orange-600 font-medium'
                            : 'bg-yellow-500/20 hover:bg-yellow-500/30'
                    } disabled:opacity-50`}
                >
                    {restartingEngine ? (
                        <Loader2 className="w-3 h-3 mr-1.5 animate-spin" />
                    ) : (
                        <RefreshCw className="w-3 h-3 mr-1.5" />
                    )}
                    {restartingEngine ? 'Restarting...' : 'Restart AI Engine'}
                </button>
            </div>
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Tools & Capabilities</h1>
                    <p className="text-muted-foreground mt-1">
                        Configure the tools and capabilities available to the AI agent.
                    </p>
                </div>
                <button
                    onClick={handleSave}
                    disabled={saving}
                    className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                >
                    <Save className="w-4 h-4 mr-2" />
                    {saving ? 'Saving...' : 'Save Changes'}
                </button>
            </div>

            {/* Phase Tabs */}
            <div className="border-b border-border">
                <div className="flex space-x-1">
                    <button
                        onClick={() => setActivePhase('pre_call')}
                        className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                            activePhase === 'pre_call'
                                ? 'border-primary text-primary'
                                : 'border-transparent text-muted-foreground hover:text-foreground hover:border-border'
                        }`}
                    >
                        <Search className="w-4 h-4" />
                        Pre-Call
                    </button>
                    <button
                        onClick={() => setActivePhase('in_call')}
                        className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                            activePhase === 'in_call'
                                ? 'border-primary text-primary'
                                : 'border-transparent text-muted-foreground hover:text-foreground hover:border-border'
                        }`}
                    >
                        <Phone className="w-4 h-4" />
                        In-Call
                    </button>
                    <button
                        onClick={() => setActivePhase('post_call')}
                        className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                            activePhase === 'post_call'
                                ? 'border-primary text-primary'
                                : 'border-transparent text-muted-foreground hover:text-foreground hover:border-border'
                        }`}
                    >
                        <Webhook className="w-4 h-4" />
                        Post-Call
                    </button>
                </div>
            </div>

            {/* Pre-Call Phase */}
            {activePhase === 'pre_call' && (
                <ConfigSection 
                    title="Pre-Call Tools" 
                    description="Tools that run before the AI speaks. Use for CRM lookups, caller enrichment, and context injection."
                >
                    <ConfigCard>
                        <HTTPToolForm
                            config={config.tools || {}}
                            onChange={(newTools) => setConfig({ ...config, tools: newTools })}
                            phase="pre_call"
                        />
                    </ConfigCard>
                </ConfigSection>
            )}

            {/* In-Call Phase (existing tools + HTTP tools) */}
            {activePhase === 'in_call' && (
                <>
                    <ConfigSection title="Built-in Tools" description="Core tools available during the conversation (transfer, hangup, email, etc.)">
                        <ConfigCard>
                            <ToolForm
                                config={{ ...(config.tools || {}), farewell_hangup_delay_sec: config.farewell_hangup_delay_sec }}
                                onChange={updateToolsConfig}
                            />
                        </ConfigCard>
                    </ConfigSection>
                    <ConfigSection 
                        title="In-Call HTTP Tools" 
                        description="HTTP lookup tools the AI can invoke during conversation to fetch data (e.g., check availability, lookup order status)."
                    >
                        <ConfigCard>
                            <HTTPToolForm
                                config={config.in_call_tools || {}}
                                onChange={(newTools) => setConfig({ ...config, in_call_tools: newTools })}
                                phase="in_call"
                            />
                        </ConfigCard>
                    </ConfigSection>
                </>
            )}

            {/* Post-Call Phase */}
            {activePhase === 'post_call' && (
                <ConfigSection 
                    title="Post-Call Tools" 
                    description="Tools that run after the call ends. Use for webhooks, CRM updates, and integrations."
                >
                    <ConfigCard>
                        <HTTPToolForm
                            config={config.tools || {}}
                            onChange={(newTools) => setConfig({ ...config, tools: newTools })}
                            phase="post_call"
                        />
                    </ConfigCard>
                </ConfigSection>
            )}
        </div>
    );
};

export default ToolsPage;
