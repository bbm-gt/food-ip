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

export interface Material {
  shot_index: number
  filename: string
  duration: number
  width: number
  height: number
  fps: number
  has_audio: boolean
}

export interface ShotEdit {
  trim_head: number
  trim_tail: number
}

export type Transition = 'hard' | 'fade' | 'crossfade'

export interface JunctionEdit {
  transition: Transition
  fade_seconds: number
}

export interface Edits {
  shots: ShotEdit[]
  junctions: JunctionEdit[]
}

export interface TimelineSegment extends ShotEdit {
  shot_index: number
  source_duration: number
  used_duration: number
  start: number
  end: number
}

export interface TimelineJunction extends JunctionEdit {
  index: number
  offset: number | null
}

export interface Timeline {
  segments: TimelineSegment[]
  junctions: TimelineJunction[]
  total_duration: number
}

export interface PutEditsResponse {
  edits: Edits
  timeline: Timeline
}

export interface PutJunctionBody {
  trim_tail: number
  trim_head: number
  transition: Transition
  fade_seconds: number
}

export type ExportJobStatus = 'pending' | 'running' | 'done' | 'failed'

export interface ExportJob {
  status: ExportJobStatus
  progress: number
  message: string
  result: {
    output: string
    total_duration: number
  } | null
}
