import { exportUrl, getThumbnailUrl, previewJunctionUrl } from '../api/client'
import type { Bgm, Edits, ExportJob, JunctionEdit, Material, Project, ScriptModel, Timeline } from '../api/types'

interface MaterialsViewProps {
  project: Project
  script: ScriptModel | null
  materials: Material[]
  timeline: Timeline | null
  busy: boolean
  canEdit: boolean
  onScript: () => void
  onEdit: () => void
  onUpload: (shotIndex: number, file: File, replacing: boolean) => void
  onDelete: (shotIndex: number) => void
}

interface MaterialTaskShot {
  shot_index: number
  lines: string
  location: string
  angle: string
}

export function MaterialsView({ project, script, materials, timeline, busy, canEdit, onScript, onEdit, onUpload, onDelete }: MaterialsViewProps) {
  const materialByShot = new Map(materials.map((material) => [material.shot_index, material]))
  const taskShots: MaterialTaskShot[] = script?.shots.length
    ? script.shots.map((shot) => ({
      shot_index: shot.shot_index,
      lines: shot.lines,
      location: shot.location,
      angle: shot.angle,
    }))
    : materials.map((material) => ({
      shot_index: material.shot_index,
      lines: '',
      location: '',
      angle: '',
    }))
  const completedCount = taskShots.filter((shot) => materialByShot.has(shot.shot_index)).length

  return (
    <section className="materials-page">
      <div className="script-toolbar"><div><p className="eyebrow">MATERIALS & TIMELINE</p><h1>{project.name}</h1><p>请按脚本镜头分别上传，素材会绑定到对应的 shot_index。</p></div>
        <div className="toolbar-actions">{script && <button className="ghost-button" type="button" onClick={onScript}>返回脚本</button>}{canEdit && <button className="primary-button" type="button" onClick={onEdit}>继续缝合 →</button>}
        </div>
      </div>
      {script?.shots.length ? <div className="material-progress"><strong>素材完成度</strong><span>{completedCount} / {taskShots.length} 个镜头已完成</span><div className="material-progress-track"><div style={{ width: `${taskShots.length ? completedCount / taskShots.length * 100 : 0}%` }} /></div></div> : null}
      {Boolean(script?.shots.length) && !canEdit && <div className="notice error" role="alert">请先为当前脚本的每个镜头上传素材后再继续缝合。</div>}
      {!script?.shots.length && materials.length >= 2 && !canEdit && <div className="notice error" role="alert">镜头编号不连续，请上传缺失的镜头后再继续缝合。</div>}
      <div className="timeline-panel"><div className="timeline-heading"><div><strong>成片时间轴</strong><small>每段宽度按后端返回的可用时长展示</small></div><span>{(timeline?.total_duration ?? 0).toFixed(2)} 秒</span></div>
        {timeline?.segments.length ? <div className="timeline-track">{timeline.segments.map((segment) => <div className="timeline-segment" key={segment.shot_index} style={{ flexGrow: segment.used_duration }} title={`镜头 ${segment.shot_index}：${segment.used_duration.toFixed(2)} 秒`}><span>{segment.shot_index}</span><small>{segment.used_duration.toFixed(1)}s</small></div>)}</div> : <div className="timeline-empty">上传素材后，这里会显示权威时间轴。</div>}
      </div>
      {taskShots.length === 0 ? <div className="empty-state compact"><span>📹</span><h2>还没有可上传的镜头</h2><p>请先生成或选择脚本。</p></div> : (
        <div className="material-task-grid">{taskShots.map((shot) => {
          const material = materialByShot.get(shot.shot_index)
          const inputId = `material-upload-${project.id}-${shot.shot_index}`
          return <article className={`material-task ${material ? 'complete' : 'pending'}`} key={shot.shot_index}>
            <div className="material-task-heading"><span className="material-index">镜头 {shot.shot_index}</span><strong>{material ? '已上传' : '待上传'}</strong></div>
            <div className="material-task-body">
              {material ? <img src={getThumbnailUrl(project.id, material.shot_index)} alt={`镜头 ${material.shot_index} 缩略图`} /> : <div className="material-placeholder">等待上传视频</div>}
              <div className="material-task-copy"><h2>{shot.location || `镜头 ${shot.shot_index}`}</h2><small>{shot.angle || '未指定机位'}</small><p>{shot.lines || '请根据脚本完成这个镜头的拍摄。'}</p>{material && <div className="material-file-info"><strong>{material.filename}</strong><span>{material.duration.toFixed(2)} 秒 · {material.width}×{material.height} · {material.fps.toFixed(1)} fps</span></div>}</div>
            </div>
            <div className="material-task-actions"><label className={`upload-button ${busy ? 'disabled' : ''}`} htmlFor={inputId}>{material ? '替换素材' : '上传素材'}<input id={inputId} type="file" accept=".mp4,.mov,.mkv,.avi" disabled={busy} onChange={(event) => { const file = event.target.files?.[0]; if (file) onUpload(shot.shot_index, file, Boolean(material)); event.target.value = '' }} /></label>{material && <button className="ghost-button" type="button" disabled={busy} onClick={() => onDelete(material.shot_index)}>删除</button>}</div>
          </article>
        })}</div>
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

interface ExportViewProps {
  project: Project
  timeline: Timeline | null
  job: ExportJob | null
  hasExistingExport: boolean
  bgm: Bgm | null
  busy: boolean
  bgmBusy: boolean
  onEdit: () => void
  onExport: () => void
  onBgmUpload: (file: File) => void
  onBgmDelete: () => void
}

export function ExportView({ project, timeline, job, hasExistingExport, bgm, busy, bgmBusy, onEdit, onExport, onBgmUpload, onBgmDelete }: ExportViewProps) {
  const canDownload = hasExistingExport || job?.status === 'done'
  return <section className="export-page"><div className="script-toolbar"><div><p className="eyebrow">FINAL EXPORT</p><h1>导出竖屏成片</h1><p>1080×1920 · 30 fps · H.264 + AAC</p></div><button className="ghost-button" type="button" onClick={onEdit}>返回接缝编辑</button></div>
    <div className="export-card"><div className="export-icon">↗</div><h2>{canDownload ? '成片已就绪' : '准备生成最终视频'}</h2><p>{job?.message ?? (hasExistingExport ? '已从项目中恢复已有成片，可以直接下载。' : `预计总时长 ${(timeline?.total_duration ?? 0).toFixed(2)} 秒`)}</p>
      <div className="bgm-control"><div><strong>BGM</strong><small>{bgm ? `${bgm.original_filename} · ${bgm.duration.toFixed(1)} 秒` : '可选，一个项目一个背景音乐，固定低音量混入'}</small></div><div className="toolbar-actions"><label className={`upload-button ${bgmBusy ? 'disabled' : ''}`}><input type="file" accept=".aac,.flac,.m4a,.mp3,.ogg,.wav" disabled={bgmBusy} onChange={(event) => { const file = event.target.files?.[0]; if (file) onBgmUpload(file); event.target.value = '' }} />{bgm ? '替换 BGM' : '上传 BGM'}</label>{bgm && <button className="ghost-button" type="button" disabled={bgmBusy} onClick={onBgmDelete}>删除</button>}</div></div>
      {job?.status === 'failed' && <div className="notice error export-error" role="alert">导出失败：{job.message || '请检查素材后重试'}</div>}
      {job?.status === 'done' && job.result?.warnings?.map((warning) => <div className="notice" role="note" key={warning}>{warning}</div>)}
      {job && <div className="progress-block"><div className="progress-label"><span>渲染进度</span><strong>{Math.round(job.progress)}%</strong></div><div className="progress-track" role="progressbar" aria-valuenow={job.progress}><div style={{ width: `${Math.min(100, Math.max(0, job.progress))}%` }} /></div></div>}
      <div className="export-actions">{canDownload && <a className="download-button" href={exportUrl(project.id)} download="final.mp4">下载 final.mp4</a>}{job?.status !== 'done' && <button className={canDownload ? 'ghost-button' : 'primary-button export-button'} type="button" disabled={busy || job?.status === 'pending' || job?.status === 'running'} onClick={onExport}>{canDownload || job?.status === 'failed' ? '重新导出' : '导出成片'}</button>}</div>
    </div>
  </section>
}
