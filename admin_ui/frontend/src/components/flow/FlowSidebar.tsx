import React from 'react';
import {
    Play,
    MessageSquare,
    Mic,
    GitFork,
    PhoneOff,
    Hash,
    ArrowRightLeft,
    Bot
} from 'lucide-react';

const SidebarItem = ({ type, label, icon: Icon, color }: { type: string, label: string, icon: any, color: string }) => {
    const onDragStart = (event: React.DragEvent, nodeType: string) => {
        event.dataTransfer.setData('application/reactflow', nodeType);
        event.dataTransfer.effectAllowed = 'move';
    };

    return (
        <div
            className="flex items-center gap-3 p-3 mb-2 bg-white border border-gray-200 rounded-lg cursor-grab hover:shadow-md transition-shadow"
            onDragStart={(event) => onDragStart(event, type)}
            draggable
            style={{ borderLeft: `4px solid ${color}` }}
        >
            <div style={{ color }}>
                <Icon size={18} />
            </div>
            <span className="text-sm font-medium text-gray-700">{label}</span>
        </div>
    );
};

export const FlowSidebar = () => {
    return (
        <div className="w-64 bg-gray-50 border-r border-gray-200 p-4 flex flex-col h-full overflow-y-auto">
            <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-4">Nodes</h3>

            <div className="space-y-1">
                <SidebarItem type="start" label="Start" icon={Play} color="#10B981" />
                <SidebarItem type="speak" label="Speak" icon={MessageSquare} color="#3B82F6" />
                <SidebarItem type="listen" label="Listen" icon={Mic} color="#8B5CF6" />
                <SidebarItem type="decision" label="Decision" icon={GitFork} color="#F59E0B" />
                <SidebarItem type="counter" label="Counter" icon={Hash} color="#D97706" />
                <SidebarItem type="handoff" label="Handoff" icon={ArrowRightLeft} color="#14B8A6" />
                <SidebarItem type="amd" label="AMD" icon={Bot} color="#059669" />
                <SidebarItem type="end" label="End" icon={PhoneOff} color="#EF4444" />
            </div>

            <div className="mt-8 text-xs text-gray-400">
                Drag and drop nodes onto the canvas to build your flow.
            </div>
        </div>
    );
};
