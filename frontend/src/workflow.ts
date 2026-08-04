import type {
  CreativeConversation,
  ExportJobStatus,
  IPProfile,
  Material,
  Project,
  ScriptModel,
} from './api/types'

export type ProjectStageKey = 'research' | 'ip' | 'creative' | 'script' | 'materials' | 'video'
export type ProjectStageStatus = 'complete' | 'current' | 'pending' | 'skipped'
export type ResumeView = 'setup' | 'candidates' | 'materials' | 'edit' | 'export'

export interface ProjectStage {
  key: ProjectStageKey
  label: string
  status: ProjectStageStatus
  detail: string
}

export interface ProjectWorkflow {
  stages: ProjectStage[]
  resumeView: ResumeView
  hasCompleteMaterials: boolean
  hasFinalVideo: boolean
}

interface WorkflowSource {
  project: Project
  materials: Material[]
  ipProfile: IPProfile | null
  conversations: CreativeConversation[]
  exports: string[]
  exportStatus?: ExportJobStatus
}

function hasContiguousMaterials(materials: Material[]) {
  return materials.length >= 2 && materials.every((item, index) => (
    index === 0 || item.shot_index === materials[index - 1].shot_index + 1
  ))
}

export function hasCompleteMaterials(materials: Material[], script: ScriptModel | null) {
  if (!script?.shots.length) return hasContiguousMaterials(materials)
  const materialIndexes = new Set(materials.map((material) => material.shot_index))
  return script.shots.length >= 2 && script.shots.every((shot) => materialIndexes.has(shot.shot_index))
}

function stage(
  key: ProjectStageKey,
  label: string,
  status: ProjectStageStatus,
  detail: string,
): ProjectStage {
  return { key, label, status, detail }
}

export function deriveProjectWorkflow({
  project,
  materials,
  ipProfile,
  conversations,
  exports,
  exportStatus,
}: WorkflowSource): ProjectWorkflow {
  const researchComplete = Boolean(
    project.research?.store.restaurant_name.trim()
      && project.research.store.signature_dishes.length,
  )
  const ipComplete = Boolean(ipProfile?.confirmed)
  const hasConversation = conversations.length > 0
  const creativeComplete = Boolean(project.script_bundle)
  const scriptComplete = Boolean(project.script)
  const materialComplete = hasCompleteMaterials(materials, project.script)
  const hasFinalVideo = exports.includes('final.mp4') || exportStatus === 'done'

  const hasAfterResearch = ipComplete || hasConversation || creativeComplete || scriptComplete
    || materials.length > 0 || hasFinalVideo
  const hasAfterIp = hasConversation || creativeComplete || scriptComplete
    || materials.length > 0 || hasFinalVideo
  const hasAfterCreative = scriptComplete || materials.length > 0 || hasFinalVideo
  const hasAfterScript = materials.length > 0 || hasFinalVideo

  const stages: ProjectStage[] = [
    stage(
      'research',
      '调研',
      researchComplete ? 'complete' : hasAfterResearch ? 'skipped' : 'current',
      researchComplete ? '核心档案已保存' : hasAfterResearch ? '旧项目未记录完整调研' : '等待补充门店档案',
    ),
    stage(
      'ip',
      'IP定位',
      ipComplete ? 'complete' : hasAfterIp ? 'skipped' : researchComplete ? 'current' : 'pending',
      ipComplete ? '定位已经确认' : hasAfterIp ? '旧流程未记录确认结果' : '等待确认定位',
    ),
    stage(
      'creative',
      'AI共创',
      creativeComplete ? 'complete' : hasAfterCreative ? 'skipped' : hasConversation ? 'current' : ipComplete ? 'current' : 'pending',
      creativeComplete ? '候选方案已生成' : hasConversation ? '已有共创对话' : hasAfterCreative ? '旧流程未记录共创过程' : '等待开始共创',
    ),
    stage(
      'script',
      '脚本',
      scriptComplete ? 'complete' : hasAfterScript ? 'skipped' : creativeComplete ? 'current' : 'pending',
      scriptComplete ? `${project.script?.shots.length ?? 0} 个镜头` : hasAfterScript ? '旧项目未保留脚本' : '等待选择脚本',
    ),
    stage(
      'materials',
      '素材',
      materialComplete ? 'complete' : hasFinalVideo ? 'skipped' : scriptComplete || materials.length > 0 ? 'current' : 'pending',
      materialComplete ? '镜头素材已齐全' : hasFinalVideo ? '成片存在，旧素材记录不完整' : materials.length ? `已上传 ${materials.length} 个镜头` : '等待上传素材',
    ),
    stage(
      'video',
      '视频',
      hasFinalVideo ? 'complete' : materialComplete ? 'current' : 'pending',
      hasFinalVideo ? '成片可下载' : materialComplete ? '可以编辑并生成视频' : '等待素材齐全',
    ),
  ]

  const resumeView: ResumeView = hasFinalVideo
    ? 'export'
    : materialComplete
      ? 'edit'
      : scriptComplete
        ? 'materials'
        : creativeComplete
          ? 'candidates'
          : 'setup'

  return { stages, resumeView, hasCompleteMaterials: materialComplete, hasFinalVideo }
}
