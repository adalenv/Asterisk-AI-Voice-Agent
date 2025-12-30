import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';

export type DecisionNodeData = {
    label: string;
    branches?: string[]; // List of output branch names
};

const DecisionNode = ({ data, id }: NodeProps<DecisionNodeData>) => {
    const branches = data.branches && data.branches.length > 0 ? data.branches : ['default'];

    return (
        <div className="px-4 py-2 shadow-md rounded-md bg-white border-2 border-orange-400 min-w-[150px]">
            <div className="flex flex-col">
                <div className="font-bold text-center text-sm mb-2">{data.label}</div>

                {/* Main Input */}
                <Handle
                    type="target"
                    position={Position.Top}
                    className="w-3 h-3 bg-gray-500"
                />

                {/* Dynamic Outputs */}
                <div className="flex justify-between gap-2 mt-2">
                    {branches.map((branch, index) => (
                        <div key={`${id}-branch-${branch}`} className="relative flex flex-col items-center">
                            <div className="text-xs text-gray-500 mb-1">{branch}</div>
                            <Handle
                                type="source"
                                position={Position.Bottom}
                                id={`source-${branch}`}
                                className="w-3 h-3 bg-orange-500 !relative !transform-none !left-0 !bottom-0"
                            />
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
};

export default memo(DecisionNode);
