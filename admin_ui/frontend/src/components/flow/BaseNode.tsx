import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { LucideIcon } from 'lucide-react';

export type BaseNodeData = {
    label: string;
    subLabel?: string;
    icon?: LucideIcon;
    color?: string;
    [key: string]: any;
};

// Props passed to the BaseNode wrapper
type BaseNodeProps = {
    icon: LucideIcon;
    color: string;
    label: string;
    subLabel?: string;
    selected?: boolean;
    isConnectable?: boolean;
    children?: React.ReactNode;
    handles?: React.ReactNode;
    className?: string; // Additional classes
};

export const BaseNodeWrapper: React.FC<BaseNodeProps> = ({
    icon: Icon,
    color,
    label,
    subLabel,
    selected,
    children,
    handles,
    className = ""
}) => {
    return (
        <div
            className={`relative min-w-[180px] rounded-lg bg-white shadow-sm border transition-shadow group ${className}`}
            style={{
                borderColor: selected ? color : '#E5E7EB', // Highlight with color when selected
                borderLeftWidth: '4px',
                borderLeftColor: color,
                boxShadow: selected ? `0 0 0 2px ${color}20` : undefined
            }}
        >
            <div className="p-3">
                <div className="flex items-center gap-3 mb-2">
                    <div
                        className="p-1.5 rounded-md"
                        style={{ backgroundColor: `${color}15`, color: color }}
                    >
                        <Icon size={16} />
                    </div>
                    <div>
                        <div className="text-sm font-semibold text-gray-900 leading-none">{label}</div>
                        {subLabel && <div className="text-[10px] text-gray-500 mt-0.5">{subLabel}</div>}
                    </div>
                </div>

                {children && (
                    <div className="text-xs text-gray-600 mt-2 pt-2 border-t border-gray-100">
                        {children}
                    </div>
                )}
            </div>

            {/* Default Handles if not provided */}
            {!handles && (
                <>
                    <Handle
                        type="target"
                        position={Position.Top}
                        className="w-2 h-2 !bg-gray-400 transition-colors group-hover:!bg-gray-600"
                    />
                    <Handle
                        type="source"
                        position={Position.Bottom}
                        className="w-2 h-2 !bg-gray-400 transition-colors group-hover:!bg-gray-600"
                    />
                </>
            )}

            {handles}
        </div>
    );
};
