import React, { useEffect, useState } from 'react';
import { Node } from 'reactflow';
import { X, Plus, Trash2, HelpCircle, FileText } from 'lucide-react';

type NodeConfigPanelProps = {
    selectedNode: Node | null;
    onUpdateNode: (nodeId: string, data: any) => void;
    onClose: () => void;
};

const NodeConfigPanel: React.FC<NodeConfigPanelProps> = ({ selectedNode, onUpdateNode, onClose }) => {
    const [label, setLabel] = useState('');
    const [branches, setBranches] = useState<string[]>([]);
    const [newBranch, setNewBranch] = useState('');
    const [prompt, setPrompt] = useState('');
    const [text, setText] = useState('');
    const [keywords, setKeywords] = useState('');

    useEffect(() => {
        if (selectedNode) {
            setLabel(selectedNode.data.label || '');
            setBranches(selectedNode.data.branches || ['match', 'nomatch']);
            setPrompt(selectedNode.data.prompt || '');
            setText(selectedNode.data.text || '');
            setKeywords(selectedNode.data.keywords || '');
        } else {
            setLabel('');
            setBranches([]);
            setPrompt('');
            setText('');
            setKeywords('');
        }
    }, [selectedNode]);

    if (!selectedNode) return null;

    const handleUpdate = (field: string, value: any) => {
        onUpdateNode(selectedNode.id, { ...selectedNode.data, [field]: value });
    };

    const isDecision = selectedNode.type === 'decision';
    const isStart = selectedNode.type === 'start' || selectedNode.type === 'intro';
    const isSpeak = selectedNode.type === 'speak';

    return (
        <div className="w-80 bg-white border-l border-gray-200 h-full flex flex-col shadow-xl z-10 transition-transform">
            {/* Header */}
            <div className="flex items-center justify-between p-4 border-b bg-white">
                <h3 className="font-semibold text-gray-900">{selectedNode.type ? selectedNode.type.charAt(0).toUpperCase() + selectedNode.type.slice(1) : 'Node'} Configuration</h3>
                <button onClick={onClose} className="p-1 hover:bg-gray-100 rounded text-gray-400 hover:text-gray-600 transition">
                    <X size={18} />
                </button>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto p-5 space-y-6">

                {/* Common: Label */}
                <div className="space-y-1.5">
                    <label className="text-sm font-medium text-gray-700 block">Label</label>
                    <input
                        type="text"
                        value={label}
                        onChange={(e) => {
                            setLabel(e.target.value);
                            handleUpdate('label', e.target.value);
                        }}
                        className="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-shadow text-sm"
                        placeholder="Node Name"
                    />
                </div>

                {/* Start/Intro: System Prompt */}
                {isStart && (
                    <div className="space-y-1.5">
                        <div className="flex items-center justify-between">
                            <label className="text-sm font-medium text-gray-700 block">System Prompt</label>
                            <HelpCircle size={14} className="text-gray-400" />
                        </div>
                        <textarea
                            value={prompt}
                            onChange={(e) => {
                                setPrompt(e.target.value);
                                handleUpdate('prompt', e.target.value);
                            }}
                            className="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 outline-none min-h-[120px] text-sm resize-y"
                            placeholder="Enter the underlying system prompt for the AI..."
                        />
                    </div>
                )}

                {/* Speak: TTS Text */}
                {isSpeak && (
                    <div className="space-y-1.5">
                        <div className="flex items-center justify-between">
                            <label className="text-sm font-medium text-gray-700 block">TTS Text</label>
                            <FileText size={14} className="text-gray-400" />
                        </div>
                        <textarea
                            value={text}
                            onChange={(e) => {
                                setText(e.target.value);
                                handleUpdate('text', e.target.value);
                            }}
                            className="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 outline-none min-h-[100px] text-sm resize-y"
                            placeholder="Text for the AI to speak..."
                        />
                    </div>
                )}

                {/* Decision: Branching */}
                {isDecision && (
                    <div className="space-y-6">

                        <div className="space-y-1.5">
                            <label className="text-sm font-medium text-gray-700 block">Evaluation Method</label>
                            <select className="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 outline-none text-sm bg-white">
                                <option>Keyword Match</option>
                                <option>LLM Classification</option>
                            </select>
                        </div>

                        <div className="space-y-2">
                            <label className="text-sm font-medium text-gray-700 block">Branches</label>
                            {branches.map((branch, idx) => (
                                <div key={idx} className="bg-gray-50 p-3 rounded-md border border-gray-200">
                                    <div className="flex items-center justify-between mb-2">
                                        <div className="flex items-center gap-2">
                                            <div className={`w-2 h-2 rounded-full ${branch === 'match' ? 'bg-green-500' : branch === 'nomatch' ? 'bg-red-500' : 'bg-blue-500'}`}></div>
                                            <span className="text-sm font-medium text-gray-700 capitalize">{branch}</span>
                                        </div>
                                        <button onClick={() => {
                                            const newBranches = branches.filter(b => b !== branch);
                                            setBranches(newBranches);
                                            handleUpdate('branches', newBranches);
                                        }} className="text-gray-400 hover:text-red-500">
                                            <Trash2 size={14} />
                                        </button>
                                    </div>
                                    {/* Mock Keywords input for visual match to reference */}
                                    <input
                                        type="text"
                                        className="w-full px-2 py-1 text-xs border border-gray-200 rounded bg-white"
                                        placeholder="Comma-separated keywords..."
                                    />
                                </div>
                            ))}
                        </div>

                        {/* Add Branch */}
                        <div className="flex gap-2">
                            <input
                                type="text"
                                value={newBranch}
                                onChange={(e) => setNewBranch(e.target.value)}
                                placeholder="New branch name"
                                className="flex-1 px-3 py-1.5 border border-gray-300 rounded text-sm focus:ring-1 focus:ring-blue-500 outline-none"
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter' && newBranch) {
                                        const newBranches = [...branches, newBranch];
                                        setBranches(newBranches);
                                        handleUpdate('branches', newBranches);
                                        setNewBranch('');
                                    }
                                }}
                            />
                            <button
                                onClick={() => {
                                    if (newBranch) {
                                        const newBranches = [...branches, newBranch];
                                        setBranches(newBranches);
                                        handleUpdate('branches', newBranches);
                                        setNewBranch('');
                                    }
                                }}
                                className="px-3 py-1.5 bg-gray-100 text-gray-700 rounded border border-gray-300 hover:bg-gray-200 transition"
                            >
                                <Plus size={16} />
                            </button>
                        </div>
                    </div>
                )}
            </div>

            <div className="p-4 border-t bg-gray-50 text-xs text-center text-gray-400">
                {selectedNode.id} • {selectedNode.position.x.toFixed(0)}, {selectedNode.position.y.toFixed(0)}
            </div>
        </div>
    );
};

export default NodeConfigPanel;
