export type Task = { id:string; title:string; description:string; category:string; due_at:string|null; priority:string; status:string; created_at:string; opportunity_id:string };
export type Opportunity = { id:string; title:string; organization:string; type:string; deadline:string|null; url:string; location:string; remote_mode:string; description:string; eligibility:string; skills:string; source:string; stipend:string; status:string; is_verified:number; created_at:string; linked_tasks?:Task[] };
export type Call = { id:string; phone:string; purpose:string; consent:number; status:string; transcript:string; created_at:string };
export type Briefing = { headline:string; detail:string; suggested_action?:string; source_type:string; source_id?:string|null; source_title:string|null; rule:string; route?:string };
export const API = process.env.NEXT_PUBLIC_AURA_API_URL || "http://localhost:8000";
let csrfToken: string | null = null;
export function setCsrfToken(token: string | null) { csrfToken = token; }
export async function api<T>(path:string, options:RequestInit={}):Promise<T>{
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  const method = (options.method || "GET").toUpperCase();
  if (csrfToken && ["POST", "PUT", "PATCH", "DELETE"].includes(method) && path !== "/api/auth/login") headers.set("X-CSRF-Token", csrfToken);
  const res=await fetch(`${API}${path}`,{...options,credentials:"include",headers});
  const data=await res.json().catch(()=>({}));
  if(!res.ok) throw new Error(typeof data.detail==="string"?data.detail:`Request failed (${res.status})`);
  return data as T;
}

export type Stats={tasks:{total:number;open:number;in_progress:number;completed:number;completion_rate_pct:number};opportunities:{total:number;saved:number;interested:number;applied:number;shortlisted:number;upcoming_7d:number};calls_count:number;recent_activity_count:number};
