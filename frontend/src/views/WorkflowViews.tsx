import type { Project, ScriptModel } from '../api/types'
import type { ProjectWorkflow } from '../workflow'

const STATUS_LABELS = {
  complete: '已完成',
  current: '进行中',
  pending: '未开始',
  skipped: '已跳过',
} as const

export function ProjectFlowProgress({ workflow }: { workflow: ProjectWorkflow }) {
  return <nav className="project-flow" aria-label="项目流程">
    <ol>{workflow.stages.map((item, index) => <li className={item.status} key={item.key}>
      <span className="flow-index">{item.status === 'complete' ? '✓' : index + 1}</span>
      <span className="flow-copy"><strong>{item.label}</strong><small>{item.detail}</small></span>
      <span className="flow-status">{STATUS_LABELS[item.status]}</span>
    </li>)}</ol>
  </nav>
}

function Value({ children }: { children: string }) {
  return <p>{children.trim() || '未填写'}</p>
}

interface ShootingChecklistViewProps {
  project: Project
  script: ScriptModel
  onScript: () => void
  onMaterials: () => void
}

export function ShootingChecklistView({
  project,
  script,
  onScript,
  onMaterials,
}: ShootingChecklistViewProps) {
  return <section className="shooting-checklist-page">
    <div className="script-toolbar">
      <div><p className="eyebrow">SHOOTING CHECKLIST</p><h1>{project.name} · 拍摄清单</h1><p>按镜头照着拍、照着说，拍完后直接上传对应素材。</p></div>
      <div className="toolbar-actions"><button className="ghost-button" type="button" onClick={onScript}>返回脚本</button><button className="primary-button" type="button" onClick={onMaterials}>上传素材</button></div>
    </div>
    <div className="shooting-checklist">{script.shots.map((shot) => <article className="checklist-shot" key={shot.shot_index}>
      <header><span className="shot-index">{String(shot.shot_index).padStart(2, '0')}</span><div><strong>{shot.location || `镜头 ${shot.shot_index}`}</strong><small>{shot.angle} · 约 {shot.duration_hint_seconds} 秒</small></div></header>
      <div className="checklist-lines"><strong>台词</strong><Value>{shot.lines}</Value></div>
      <div className="checklist-columns">
        <section><h2>怎么拍</h2><dl>
          <div><dt>拍摄要点</dt><dd><Value>{shot.shooting_tips}</Value></dd></div>
          <div><dt>手机与机位</dt><dd><Value>{shot.phone_setup}</Value></dd></div>
          <div><dt>动作步骤</dt><dd>{shot.action_steps.length ? <ol>{shot.action_steps.map((item, index) => <li key={`${shot.shot_index}-action-${index}`}>{item}</li>)}</ol> : <Value>{''}</Value>}</dd></div>
        </dl></section>
        <section><h2>怎么说</h2><dl>
          <div><dt>语气</dt><dd><Value>{shot.tone}</Value></dd></div>
          <div><dt>情绪</dt><dd><Value>{shot.emotion}</Value></dd></div>
          <div><dt>语速</dt><dd><Value>{shot.speech_rate}</Value></dd></div>
          <div><dt>停顿</dt><dd><Value>{shot.pause_guidance}</Value></dd></div>
          <div><dt>表达方式</dt><dd><Value>{shot.expression_guidance}</Value></dd></div>
        </dl></section>
      </div>
    </article>)}</div>
  </section>
}
