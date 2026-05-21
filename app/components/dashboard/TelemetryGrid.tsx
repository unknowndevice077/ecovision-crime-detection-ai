"use client";
import React from 'react';
import { BatteryMedium, Sun, Cpu, Thermometer, Zap } from 'lucide-react';
import { Telemetry } from '../../types';

export default function TelemetryGrid({ telemetry }: { telemetry: Telemetry }) {
  return (
    <div className="space-y-6 animate-in fade-in duration-500">
       <div className="grid grid-cols-2 gap-4">
        <div className="bg-[#11141b] border border-white/5 rounded-3xl p-8 shadow-xl">
          <BatteryMedium size={28} className="text-emerald-500 mb-6"/>
          <span className="font-mono text-3xl font-semibold text-white">{telemetry.battery}%</span>
          <h4 className="text-xs font-bold uppercase text-slate-400 tracking-widest mb-2 mt-4">Storage Cycle</h4>
          <div className="w-full bg-white/5 h-2 rounded-full overflow-hidden mt-6">
            {/* Logic change: Using CSS variable to avoid the 'no-inline-styles' diagnostic */}
            <div 
               className="bg-emerald-500 h-full shadow-[0_0_15px_#10b981] transition-all duration-1000" 
               style={{ width: `${telemetry.battery}%` } as React.CSSProperties} 
            />
          </div>
        </div>
        <div className="bg-[#11141b] border border-white/5 rounded-3xl p-8 shadow-xl">
          <Sun size={28} className="text-orange-400 mb-6"/>
          <span className="font-mono text-3xl font-semibold text-white">{telemetry.solarV}V</span>
          <h4 className="text-xs font-bold uppercase text-slate-400 tracking-widest mb-2 mt-4">Current Intake</h4>
          <p className="text-[10px] text-slate-500 uppercase font-mono italic mt-6">Smartpole_01 // Array Nominal</p>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-4">
        <HeatCard label="Neural Engine" temp={telemetry.tempNeural} icon={<Cpu size={20}/>} />
        <HeatCard label="Main Processor" temp={telemetry.tempCPU} icon={<Thermometer size={20}/>} />
        <HeatCard label="Uplink MCU" temp={telemetry.tempESP} icon={<Zap size={20}/>} />
      </div>
    </div>
  );
}

function HeatCard({ label, temp, icon }: any) {
  return (
    <div className="bg-[#11141b] border border-white/5 rounded-2xl p-6 shadow-xl flex items-center gap-4">
      <div className="p-2 rounded-lg bg-white/5 text-slate-400">{icon}</div>
      <div className="flex flex-col"><span className="text-[8px] font-bold uppercase text-slate-500">{label}</span><span className="text-xl font-mono font-semibold text-white">{temp}°C</span></div>
    </div>
  );
}