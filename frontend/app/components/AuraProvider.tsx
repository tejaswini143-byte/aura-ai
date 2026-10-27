"use client";
import { createContext,useCallback,useContext,useEffect,useState,ReactNode } from "react";
import { api,Task,Opportunity,Call,Briefing,Stats,setCsrfToken } from "../api";
type Store={stats:Stats|null;tasks:Task[];opportunities:Opportunity[];calls:Call[];briefing:Briefing|null;online:boolean|null;loading:boolean;error:string;refresh:()=>Promise<void>;run:(path:string,method:string,body?:unknown)=>Promise<any>;notice:(s:string)=>void;message:string};
const Context=createContext<Store|null>(null);
export function AuraProvider({children}:{children:ReactNode}){
 const [stats,setStats]=useState<Stats|null>(null);
 const [tasks,setTasks]=useState<Task[]>([]),[opportunities,setOpps]=useState<Opportunity[]>([]),[calls,setCalls]=useState<Call[]>([]),[briefing,setBriefing]=useState<Briefing|null>(null);
 const [online,setOnline]=useState<boolean|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState(""),[message,setMessage]=useState("");
 const refresh=useCallback(async()=>{try{const [t,o,c,b,s]=await Promise.all([api<Task[]>("/api/tasks"),api<Opportunity[]>("/api/opportunities"),api<Call[]>("/api/calls"),api<Briefing>("/api/briefing"),api<Stats>("/api/stats")]);setStats(s);setTasks(t);setOpps(o);setCalls(c);setBriefing(b);setOnline(true);setError("");}catch(e){setOnline(false);setError(e instanceof Error?e.message:"Could not load AURA data");}finally{setLoading(false);}},[]);
 useEffect(()=>{api<{csrf_token:string|null}>("/api/auth/session").then(session=>setCsrfToken(session.csrf_token)).catch(()=>setCsrfToken(null));void refresh()},[refresh]);
 const run=useCallback(async(path:string,method:string,body?:unknown)=>{const data=await api<any>(path,{method,body:body===undefined?undefined:JSON.stringify(body)});await refresh();return data},[refresh]);
 const notice=useCallback((s:string)=>{setMessage(s);window.setTimeout(()=>setMessage(""),3500)},[]);
 return <Context.Provider value={{stats,tasks,opportunities,calls,briefing,online,loading,error,refresh,run,notice,message}}>{children}</Context.Provider>
}
export function useAura(){const value=useContext(Context);if(!value)throw new Error("useAura must be used inside AuraProvider");return value}
