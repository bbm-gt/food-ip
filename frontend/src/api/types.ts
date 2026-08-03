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

export interface StoreProfile {
  restaurant_name: string
  city: string
  business_district: string
  cuisine_type: string
  years_in_business: number
  price_per_person: number
  signature_dishes: string[]
  business_modes: string[]
  differentiators: string[]
  ingredient_proofs: string[]
  visible_processes: string[]
  customer_praises: string[]
  customer_misunderstandings: string[]
}

export interface OwnerProfile {
  owner_name: string
  hometown: string
  owner_persona: string
  origin_story: string
  hardest_moment: string
  proudest_moment: string
  unique_experience: string
  speaking_style: string
  appearance_mode: '真人口播' | '旁白' | '只拍手部' | '不出镜'
  language_style: string
  avoided_topics: string[]
  allow_personal_story: boolean
}

export interface AudienceProfile {
  core_audience: string
  dining_scenarios: string[]
  customer_needs: string[]
  customer_concerns: string[]
  current_business_problem: string
  content_goal: '吸引到店' | '团购转化' | '账号涨粉' | '建立信任' | '品牌认知'
}

export interface ShootingProfile {
  platform: string
  video_style: string
  target_duration_seconds: number
  available_locations: string[]
  unavailable_locations: string[]
  can_show_kitchen: boolean
  can_show_customers: boolean
  equipment: string[]
  daily_minutes: number
  update_frequency: string
  hook_preference: string
}

export interface ResearchProfile {
  schema_version: number
  store: StoreProfile
  owner: OwnerProfile
  audience: AudienceProfile
  shooting: ShootingProfile
}

export interface Shot {
  shot_index: number
  lines: string
  shooting_tips: string
  duration_hint_seconds: number
  location: string
  angle: string
  purpose: string
  subject: string
  action_steps: string[]
  phone_setup: string
  camera_movement: string
  audio: string
  lighting: string
  props: string[]
  subtitle: string
  edit_note: string
  common_mistakes: string[]
  retake_if: string[]
}

export interface ScriptModel {
  title: string
  target_duration_seconds: number
  style: string
  opening_hook: string
  cta: string
  shots: Shot[]
}

export interface ScriptCandidate {
  id: string
  strategy: string
  strategy_name: string
  positioning: string
  score: number
  reasons: string[]
  difficulty: '简单' | '中等' | '较难'
  required_scenes: string[]
  requires_owner: boolean
  script: ScriptModel
}

export interface ScriptBundle {
  id: string
  generated_at: string
  research_summary: string
  candidates: ScriptCandidate[]
  selected_script_id: string | null
  generator: 'template' | 'ai' | 'template_fallback'
  model_name: string
  warnings: string[]
}

export interface Project {
  id: string
  name: string
  boss_info: Partial<BossInfo>
  research: ResearchProfile
  script: ScriptModel | null
  script_bundle: ScriptBundle | null
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
