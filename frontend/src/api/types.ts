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
  tone: string
  emotion: string
  speech_rate: string
  pause_guidance: string
  expression_guidance: string
}

export type ScriptQualityRiskCategory = '真实性' | '可拍摄性' | 'IP一致性'

export interface ScriptQualityRisk {
  category: ScriptQualityRiskCategory
  message: string
  shot_index: number | null
}

export interface ScriptModel {
  title: string
  target_duration_seconds: number
  style: string
  opening_hook: string
  cta: string
  shots: Shot[]
  quality_risks: ScriptQualityRisk[]
}

export type ScriptVersionSource =
  | 'legacy_import'
  | 'template_generation'
  | 'candidate_selection'
  | 'manual_save'

export interface ScriptVersion {
  id: string
  version_number: number
  created_at: string
  source: ScriptVersionSource
  script: ScriptModel
}

export type CreativeMode = 'own_idea' | 'ai_recommendation' | 'revise_script'
export type ConversationStage = 'collecting' | 'brief_ready' | 'confirmed'
export type FactScope = 'episode_only' | 'long_term_profile'
export type EvidenceSource = 'research_profile' | 'ip_profile' | 'owner_message'

export interface CreativeEvidence {
  statement: string
  source: EvidenceSource
  verified: boolean
  fact_scope: FactScope | null
}

export interface CreativeBrief {
  idea: string
  goal: string
  target_customer: string
  key_message: string
  evidence: CreativeEvidence[]
  tone: string
  format: string
  shooting_constraints: string[]
  cta: string
  confirmed: boolean
  confirmed_at: string | null
}

export interface CreativeMessage {
  id: string
  role: 'owner' | 'ai'
  content: string
  fact_scope: FactScope | null
  trust_status: 'untrusted' | 'assistant_synthesis'
  questions: string[]
  reply_to_message_id: string | null
  created_at: string
}

export interface TopicCard {
  id: string
  title: string
  hook: string
  angle: string
  target_customer: string
  ip_alignment: string
  evidence_needed: string[]
  shoot_difficulty: 'low' | 'medium' | 'high'
  estimated_duration_sec: number
  cta: string
}

export interface TopicCardSet {
  id: string
  generated_at: string
  model_name: string
  cards: TopicCard[]
  selected_topic_card_id: string | null
}

export interface IPProfile {
  persona_positioning: string
  core_audience: string
  core_promise: string
  memory_points: string[]
  content_pillars: string[]
  recurring_series: string[]
  speaking_style: string
  evidence_assets: string[]
  avoided_topics: string[]
  conversion_path: string[]
  confirmed: boolean
  confirmed_at: string | null
}

export interface CreativeConversation {
  id: string
  project_id: string
  mode: CreativeMode
  stage: ConversationStage
  research_snapshot: ResearchProfile
  ip_profile_snapshot: IPProfile
  source_script: ScriptModel | null
  messages: CreativeMessage[]
  brief: CreativeBrief | null
  topic_card_set: TopicCardSet | null
  last_error: string | null
  created_at: string
  updated_at: string
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
  research: ResearchProfile | null
  script: ScriptModel | null
  script_bundle: ScriptBundle | null
  materials?: Material[]
  edits?: Edits | null
  bgm?: Bgm | null
  created_at: string
}

export interface Bgm {
  filename: string
  original_filename: string
  duration: number
  has_audio: boolean
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
    warnings?: string[]
  } | null
}
