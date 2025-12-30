import React, { memo } from 'react';
import { NodeProps, Handle, Position } from 'reactflow';
import { BaseNodeWrapper } from './BaseNode';
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

// Start Node
export const StartNode = memo(({ data, selected }: NodeProps) => (
    <BaseNodeWrapper
        icon={Play}
        color="#10B981"
        label="Start"
        subLabel="Entry point"
        selected={selected}
    >
        {data.prompt && <div className="truncate">{data.prompt}</div>}
    </BaseNodeWrapper>
));

// Intro Node (alias for Start in this context or separate)
export const IntroNode = memo(({ data, selected }: NodeProps) => (
    <BaseNodeWrapper
        icon={Play}
        color="#10B981"
        label="Introduction"
        subLabel="Initial greeting"
        selected={selected}
    >
        {data.prompt && <div className="truncate">{data.prompt}</div>}
    </BaseNodeWrapper>
));

// Speak Node
export const SpeakNode = memo(({ data, selected }: NodeProps) => (
    <BaseNodeWrapper
        icon={MessageSquare}
        color="#3B82F6"
        label="Speak"
        subLabel="AI speaks text"
        selected={selected}
    >
        {data.text && <div className="truncate italic">"{data.text}"</div>}
    </BaseNodeWrapper>
));

// Listen Node
export const ListenNode = memo(({ data, selected }: NodeProps) => (
    <BaseNodeWrapper
        icon={Mic}
        color="#8B5CF6"
        label="Listen"
        subLabel="Wait for input"
        selected={selected}
    >
        {data.timeout && <div>Timeout: {data.timeout}s</div>}
    </BaseNodeWrapper>
));

// Decision Node - Custom Handles
export const DecisionNode = memo(({ data, selected, id }: NodeProps) => {
    const branches = data.branches || ['default'];

    return (
        <BaseNodeWrapper
            icon={GitFork}
            color="#F59E0B"
            label="Decision"
            subLabel="Branch logic"
            selected={selected}
            handles={
                <>
                    <Handle type="target" position={Position.Top} className="w-2 h-2 !bg-gray-400" />
                    <div className="flex justify-between gap-4 mt-3 pt-2 border-t border-gray-100">
                        {branches.map((branch: string) => (
                            <div key={`${id}-branch-${branch}`} className="relative flex flex-col items-center">
                                <span className={`text-[10px] uppercase font-bold mb-1 ${branch === 'default' ? 'text-gray-400' :
                                        branch === 'yes' || branch === 'match' ? 'text-green-500' :
                                            branch === 'no' || branch === 'nomatch' ? 'text-red-500' :
                                                'text-blue-500'
                                    }`}>
                                    {branch}
                                </span>
                                <Handle
                                    type="source"
                                    position={Position.Bottom}
                                    id={`source-${branch}`}
                                    className="w-2 h-2 !relative !transform-none !left-0 !bottom-0 !bg-gray-400"
                                />
                            </div>
                        ))}
                    </div>
                </>
            }
        />
    );
});

// Counter Node
export const CounterNode = memo(({ data, selected }: NodeProps) => (
    <BaseNodeWrapper
        icon={Hash}
        color="#D97706"
        label="Counter"
        subLabel="Track retries"
        selected={selected}
    />
));

// Handoff Node
export const HandoffNode = memo(({ data, selected }: NodeProps) => (
    <BaseNodeWrapper
        icon={ArrowRightLeft}
        color="#14B8A6"
        label="Handoff"
        subLabel="Transfer call"
        selected={selected}
    />
));

// AMD Node
export const AMDNode = memo(({ data, selected }: NodeProps) => (
    <BaseNodeWrapper
        icon={Bot}
        color="#059669"
        label="AMD"
        subLabel="Detect machine"
        selected={selected}
    />
));

// End Node
export const EndNode = memo(({ data, selected }: NodeProps) => (
    <BaseNodeWrapper
        icon={PhoneOff}
        color="#EF4444"
        label="End"
        subLabel="End the call"
        selected={selected}
    />
));
