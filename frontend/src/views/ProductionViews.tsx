import { exportUrl, getThumbnailUrl, previewJunctionUrl } from '../api/client'
import type { Edits, ExportJob, JunctionEdit, Material, Project, ScriptModel, Timeline } from '../api/types'

interface MaterialsViewProps {
  project: Project
  script: ScriptModel | null
  materials: Material[]
  timeline: Timeline | null
  busy: boolean
  canEdit: boolean
  onScript: () => void
  onEdit: () => void
  onUpload: (files: FileList | null) => void
  onDelete: (shotIndex: number) => void
}

export function MaterialsView({ project, script, materials, timeline, busy, canEdit, onScript, onEdit, onUpload, onDelete }: MaterialsViewProps) {
  return (
    <section className="materials-page">
      <div className="script-toolbar"><div><p className="eyebrow">MATERIALS & TIMELINE</p><h1>{project.name}</h1><p>按脚本镜头编号上传，删除素材后会优先补齐空缺编号。</p></div>
        <div className="toolbar-actions">{script && <button className="ghost-button" type="button" onClick={onScript}>返回脚本</button>}{canEdit && <button className="primary-button" type="button" onClick={onEdit}>继续缝合 →</button>}
          <label className={`upload-button ${busy ? 'disabled' : ''}`}>{busy ? '处理中…' : '＋ 上传视频'}<input type="file" multiple accept=".mp4,.mov,.mkv,.avi" disabled={busy} onChange={(event) => { onUpload(event.target.files); event.target.value = '' }} /></label>
        </div>
      </div>
      {materials.length >= 2 && !canEdit && <div className="notice error" role="alert">镜头编号不连续，请上传缺失的镜头后再继续缝合。</div>}
      <div className="timeline-panel"><div className="timeline-heading"><div><strong>成片时间轴</strong><small>每段宽度按后端返回的可用时长展示</small></div><span>{(timeline?.total_duration ?? 0).toFixed(2)} 秒</span></div>
        {timeline?.segments.length ? <div className="timeline-track">{timeline.segments.map((segment) => <div className="timeline-segment" key={segment.shot_index} style={{ flexGrow: segment.used_duration }} title={`镜头 ${segment.shot_index}：${segment.used_duration.toFixed(2)} 秒`}><span>{segment.shot_index}</span><small>{segment.used_duration.toFixed(1)}s</small></div>)}</div> : <div className="timeline-empty">上传素材后，这里会显示权威时间轴。</div>}
      </div>
      {materials.length === 0 ? <div className="empty-state compact"><span>📹</span><h2>还没有拍摄素材</h2><p>可一次选择多个视频，系统会按脚本镜头顺序依次上传。</p></div> : (
        <div className="material-grid">{materials.map((material) => <article className="material-card" key={material.shot_index}><img src={getThumbnailUrl(project.id, material.shot_index)} alt={`镜头 ${material.shot_index} 缩略图`} /><div><span className="material-index">镜头 {material.shot_index}</span><strong>{material.filename}</strong><small>{material.duration.toFixed(2)} 秒 · {material.width}×{material.height}</small></div><button className="delete-button" type="button" disabled={busy} onClick={() => onDelete(material.shot_index)}>删除</button></article>)}</div>
      )}
    </section>
  )
}

interface EditViewProps {
  project: Project
  edits: Edits
  timeline: Timeline
  selectedJunction: number
  previewVersion: number
  junctionBusy: boolean
  onSelectJunction: (index: number) => void
  onSaveJunction: (shotPatch: { trimTail?: number; trimHead?: number }, junctionPatch?: Partial<JunctionEdit>) => void
  onMaterials: () => void
  onExport: () => void
}

export function EditView({ project, edits, timeline, selectedJunction, previewVersion, junctionBusy, onSelectJunction, onSaveJunction, onMaterials, onExport }: EditViewProps) {
  const junction = edits.junctions[selectedJunction]
  return (
    <section className="edit-page">
      <div className="script-toolbar"><div><p className="eyebrow">JUNCTION EDITOR</p><h1>调好每一道接缝</h1><p>点选片段间的接缝，微调前后裁剪和转场。</p></div><div className="toolbar-actions"><button className="ghost-button" type="button" onClick={onMaterials}>返回素材</button><button className="primary-button" type="button" onClick={onExport}>继续导出 →</button></div></div>
      <div className="timeline-panel edit-timeline"><div className="timeline-heading"><div><strong>接缝时间轴</strong><small>片段宽度严格按后端 used_duration 比例显示</small></div><span>{timeline.total_duration.toFixed(2)} 秒</span></div>
        <div className="timeline-track junction-track">{timeline.segments.map((segment, index) => <div className="timeline-piece" key={segment.shot_index}><div className="timeline-segment" style={{ flexGrow: segment.used_duration }} title={`镜头 ${segment.shot_index}：${segment.used_duration.toFixed(2)} 秒`}><span>{segment.shot_index}</span><small>{segment.used_duration.toFixed(1)}s</small></div>{index < timeline.segments.length - 1 && <button className={`junction-button ${selectedJunction === index ? 'selected' : ''}`} type="button" aria-label={`编辑接缝 ${index + 1}`} onClick={() => onSelectJunction(index)}>✦</button>}</div>)}</div>
      </div>
      {junction && <div className="junction-panel">
        <div className="preview-player"><video key={`${selectedJunction}-${previewVersion}`} controls playsInline src={`${previewJunctionUrl(project.id, selectedJunction, 1.5, 1.5)}&t=${previewVersion}`} /><small>接缝 {selectedJunction + 1} · 前后各预览最多 1.5 秒</small></div>
        <div className="junction-controls">
          <TrimControl label="剪前段尾部" value={edits.shots[selectedJunction].trim_tail} busy={junctionBusy} onChange={(value) => onSaveJunction({ trimTail: value })} />
          <TrimControl label="剪后段头部" value={edits.shots[selectedJunction + 1].trim_head} busy={junctionBusy} onChange={(value) => onSaveJunction({ trimHead: value })} />
          <div className="transition-control"><label>转场<select disabled={junctionBusy} value={junction.transition} onChange={(event) => onSaveJunction({}, { transition: event.target.value as JunctionEdit['transition'] })}><option value="hard">硬切</option><option value="fade">淡入淡出</option><option value="crossfade">交叉淡化</option></select></label>
            <div className="fade-stepper"><span>Fade 时长</span><button type="button" disabled={junctionBusy || junction.transition === 'hard'} onClick={() => onSaveJunction({}, { fade_seconds: junction.fade_seconds - 0.1 })}>−0.1s</button><strong>{junction.fade_seconds.toFixed(2)}s</strong><button type="button" disabled={junctionBusy || junction.transition === 'hard'} onClick={() => onSaveJunction({}, { fade_seconds: junction.fade_seconds + 0.1 })}>+0.1s</button></div>
          </div>
        </div>
        <footer className="junction-summary"><span>{junctionBusy ? '正在保存并刷新预览…' : '所有数值由服务端自动钳制'}</span><strong>预计总时长：{timeline.total_duration.toFixed(2)} 秒</strong></footer>
      </div>}
    </section>
  )
}

function TrimControl({ label, value, busy, onChange }: { label: string; value: number; busy: boolean; onChange: (value: number) => void }) {
  return <div className="trim-control"><span>{label}</span><strong>{value.toFixed(2)}s</strong><div className="step-buttons">{[-0.2, -0.1, 0.1, 0.2].map((delta) => <button type="button" disabled={busy} key={delta} onClick={() => onChange(value + delta)}>{delta > 0 ? '+' : '−'}{Math.abs(delta).toFixed(1)}s</button>)}</div></div>
}

interface ExportViewProps { project: Project; timeline: Timeline | null; job: ExportJob | null; busy: boolean; onEdit: () => void; onExport: () => void }

export function ExportView({ project, timeline, job, busy, onEdit, onExport }: ExportViewProps) {
  return <section className="export-page"><div className="script-toolbar"><div><p className="eyebrow">FINAL EXPORT</p><h1>导出竖屏成片</h1><p>1080×1920 · 30 fps · H.264 + AAC</p></div><button className="ghost-button" type="button" onClick={onEdit}>返回接缝编辑</button></div>
    <div className="export-card"><div className="export-icon">↗</div><h2>{job?.status === 'done' ? '成片已就绪' : '准备生成最终视频'}</h2><p>{job?.message ?? `预计总时长 ${(timeline?.total_duration ?? 0).toFixed(2)} 秒`}</p>
      {job?.status === 'failed' && <div className="notice error export-error" role="alert">导出失败：{job.message || '请检查素材后重试'}</div>}
      {job && <div className="progress-block"><div className="progress-label"><span>渲染进度</span><strong>{Math.round(job.progress)}%</strong></div><div className="progress-track" role="progressbar" aria-valuenow={job.progress}><div style={{ width: `${Math.min(100, Math.max(0, job.progress))}%` }} /></div></div>}
      {job?.status === 'done' ? <a className="download-button" href={exportUrl(project.id)} download="final.mp4">下载 final.mp4</a> : <button className="primary-button export-button" type="button" disabled={busy || job?.status === 'pending' || job?.status === 'running'} onClick={onExport}>{job?.status === 'failed' ? '重新导出' : '导出成片'}</button>}
    </div>
  </section>
}
