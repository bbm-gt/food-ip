export interface BossInfo {
  restaurant_name: string
  cuisine_type: string
  signature_dishes: string[]
  owner_persona: string
  audience: string
  video_style: string
  target_duration_seconds: number
  platform: string
  hook_preference: string
}

export interface Shot {
  shot_index: number
  lines: string
  shooting_tips: string
  duration_hint_seconds: number
  location: string
  angle: string
}

export interface ScriptModel {
  title: string
  target_duration_seconds: number
  style: string
  opening_hook: string
  cta: string
  shots: Shot[]
}

export interface Project {
  id: string
  name: string
  boss_info: Partial<BossInfo>
  script: ScriptModel | null
  created_at: string
}
