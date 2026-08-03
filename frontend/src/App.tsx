import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'

import {
  createProject,
  deleteMaterial,
  exportUrl,
  generateTemplate,
  getEdits,
  getJob,
  getProject,
  getProjects,
  getThumbnailUrl,
  getTimeline,
  listMaterials,
  patchProject,
  previewJunctionUrl,
  putJunction,
  putScript,
  startExport,
  uploadMaterial,
} from './api/client'
import type {
  BossInfo,
  Edits,
  ExportJob,
  JunctionEdit,
  Material,
  Project,
  ScriptModel,
  Shot,
  Timeline,
} from './api/types'
import './app.css'

type View = 'list' | 'setup' | 'script' | 'materials' | 'edit' | 'export'

const EMPTY_FORM: BossInfo = {
  restaurant_name: '',
  cuisine_type: '家常菜',
  signature_dishes: [],
  owner_persona: '',
  audience: '',
  video_style: '竖屏口播',
  target_duration_seconds: 60,
  platform: '抖音',
  hook_preference: '',
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

export default function App() {
  const [view, setView] = useState<View>('list')
  const [projects, setProjects] = useState<Project[]>([])
  const [project, setProject] = useState<Project | null>(null)
  const [form, setForm] = useState<BossInfo>(EMPTY_FORM)
  const [dishText, setDishText] = useState('')
  const [script, setScript] = useState<ScriptModel | null>(null)
  const [materials, setMaterials] = useState<Material[]>([])
  const [edits, setEdits] = useState<Edits | null>(null)
  const [timeline, setTimeline] = useState<Timeline | null>(null)
  const [selectedJunction, setSelectedJunction] = useState(0)
  const [previewVersion, setPreviewVersion] = useState(Date.now())
  const [junctionBusy, setJunctionBusy] = useState(false)
  const [exportJobId, setExportJobId] = useState<string | null>(null)
  const [exportJob, setExportJob] = useState<ExportJob | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const previewTimer = useRef<number | null>(null)

  async function loadProjects() {
    setBusy(true)
    setError('')
    try {
      setProjects(await getProjects())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '项目加载失败')
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    void loadProjects()
  }, [])

  useEffect(() => {
    return () => {
      if (previewTimer.current !== null) window.clearTimeout(previewTimer.current)
    }
  }, [])

  useEffect(() => {
    if (!exportJobId || exportJob?.status === 'done' || exportJob?.status === 'failed') {
      return undefined
    }
    let cancelled = false
    async function pollJob() {
      try {
        const job = await getJob(exportJobId as string)
        if (!cancelled) setExportJob(job)
      } catch (reason) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : '导出进度查询失败')
        }
      }
    }
    void pollJob()
    const timer = window.setInterval(() => void pollJob(), 1000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [exportJobId, exportJob?.status])

  function startNewProject() {
    setProject(null)
    setForm(EMPTY_FORM)
    setDishText('')
    setScript(null)
    setEdits(null)
    setTimeline(null)
    setExportJobId(null)
    setExportJob(null)
    setMessage('')
    setError('')
    setView('setup')
  }

  async function openProject(item: Project) {
    setBusy(true)
    setError('')
    try {
      const fullProject = await getProject(item.id)
      setProject(fullProject)
      setForm({ ...EMPTY_FORM, ...fullProject.boss_info })
      setDishText(fullProject.boss_info.signature_dishes?.join('、') ?? '')
      setScript(fullProject.script)
      setView(fullProject.script ? 'script' : 'setup')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '项目打开失败')
    } finally {
      setBusy(false)
    }
  }

  async function submitSetup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError('')
    setMessage('')
    const bossInfo: BossInfo = {
      ...form,
      signature_dishes: dishText
        .split(/[、,，]/)
        .map((dish) => dish.trim())
        .filter(Boolean),
    }
    try {
      const activeProject =
        project ?? (await createProject(bossInfo.restaurant_name || '未命名餐饮项目'))
      await patchProject(activeProject.id, bossInfo)
      const generated = await generateTemplate(activeProject.id, bossInfo)
      setProject({ ...activeProject, boss_info: bossInfo, script: generated })
      setForm(bossInfo)
      setScript(generated)
      setView('script')
      setMessage('脚本已生成并保存。')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '脚本生成失败')
    } finally {
      setBusy(false)
    }
  }

  async function saveCurrentScript() {
    if (!project || !script) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const saved = await putScript(project.id, script)
      setScript(saved)
      setProject({ ...project, script: saved })
      setMessage('修改已保存。')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '脚本保存失败')
    } finally {
      setBusy(false)
    }
  }

  async function loadMaterials(projectId: string) {
    const [loadedMaterials, loadedTimeline] = await Promise.all([
      listMaterials(projectId),
      getTimeline(projectId),
    ])
    setMaterials(loadedMaterials)
    setTimeline(loadedTimeline)
  }

  async function openMaterials() {
    if (!project) return
    setBusy(true)
    setError('')
    setMessage('')
    setView('materials')
    try {
      await loadMaterials(project.id)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '素材加载失败')
    } finally {
      setBusy(false)
    }
  }

  async function openEdit() {
    if (!project) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const [loadedMaterials, loadedEdits, loadedTimeline] = await Promise.all([
        listMaterials(project.id),
        getEdits(project.id),
        getTimeline(project.id),
      ])
      setMaterials(loadedMaterials)
      setEdits(loadedEdits)
      setTimeline(loadedTimeline)
      setSelectedJunction(Math.min(selectedJunction, Math.max(0, loadedEdits.junctions.length - 1)))
      setPreviewVersion(Date.now())
      setView('edit')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '接缝编辑加载失败')
    } finally {
      setBusy(false)
    }
  }

  function schedulePreviewRefresh() {
    if (previewTimer.current !== null) window.clearTimeout(previewTimer.current)
    previewTimer.current = window.setTimeout(() => {
      setPreviewVersion(Date.now())
      previewTimer.current = null
    }, 400)
  }

  async function saveJunction(
    shotPatch: { trimTail?: number; trimHead?: number },
    junctionPatch: Partial<JunctionEdit> = {},
  ) {
    if (!project || !edits) return
    const left = edits.shots[selectedJunction]
    const right = edits.shots[selectedJunction + 1]
    const junction = edits.junctions[selectedJunction]
    if (!left || !right || !junction) return
    const currentTransition = junction.transition === 'hard' ? 'hard' : 'fade'
    setJunctionBusy(true)
    setError('')
    try {
      const response = await putJunction(project.id, selectedJunction, {
        trim_tail: shotPatch.trimTail ?? left.trim_tail,
        trim_head: shotPatch.trimHead ?? right.trim_head,
        transition: junctionPatch.transition ?? currentTransition,
        fade_seconds: junctionPatch.fade_seconds ?? junction.fade_seconds,
      })
      setEdits(response.edits)
      setTimeline(response.timeline)
      schedulePreviewRefresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '接缝更新失败')
    } finally {
      setJunctionBusy(false)
    }
  }

  function openExport() {
    setError('')
    setMessage('')
    setView('export')
  }

  async function beginExport() {
    if (!project) return
    setBusy(true)
    setError('')
    setMessage('')
    setExportJob(null)
    try {
      const result = await startExport(project.id)
      setExportJobId(result.job_id)
      setExportJob({ status: 'pending', progress: 0, message: '等待导出', result: null })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '导出启动失败')
    } finally {
      setBusy(false)
    }
  }

  async function uploadSelectedFiles(files: FileList | null) {
    if (!project || !files?.length) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const usedIndexes = new Set(materials.map((material) => material.shot_index))
      let nextIndex = materials.length
      for (const file of Array.from(files)) {
        while (usedIndexes.has(nextIndex)) nextIndex += 1
        const formData = new FormData()
        formData.append('shot_index', String(nextIndex))
        formData.append('file', file)
        await uploadMaterial(project.id, formData)
        usedIndexes.add(nextIndex)
        nextIndex += 1
      }
      await loadMaterials(project.id)
      setMessage(`已上传 ${files.length} 个素材。`)
    } catch (reason) {
      await loadMaterials(project.id).catch(() => undefined)
      setError(reason instanceof Error ? reason.message : '素材上传失败')
    } finally {
      setBusy(false)
    }
  }

  async function removeMaterial(shotIndex: number) {
    if (!project) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      await deleteMaterial(project.id, shotIndex)
      await loadMaterials(project.id)
      setMessage(`素材 ${shotIndex} 已删除。`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '素材删除失败')
    } finally {
      setBusy(false)
    }
  }

  function updateShot(index: number, field: keyof Shot, value: string | number) {
    setScript((current) =>
      current
        ? {
            ...current,
            shots: current.shots.map((shot, shotIndex) =>
              shotIndex === index ? { ...shot, [field]: value } : shot,
            ),
          }
        : current,
    )
  }

  async function backToProjects() {
    setView('list')
    setMessage('')
    setError('')
    await loadProjects()
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="brand" type="button" onClick={() => void backToProjects()}>
          <span className="brand-mark">食</span>
          <span>
            <strong>Food IP Studio</strong>
            <small>餐饮短视频脚本工坊</small>
          </span>
        </button>
        {view !== 'list' && (
          <button className="ghost-button" type="button" onClick={() => void backToProjects()}>
            返回项目
          </button>
        )}
      </header>

      <main className="page">
        {busy && (
          <div className="loading-indicator" role="status" aria-live="polite">
            正在处理，请稍候…
          </div>
        )}
        {error && <div className="notice error" role="alert">{error}</div>}
        {message && <div className="notice success" role="status">{message}</div>}

        {view === 'list' && (
          <section>
            <div className="page-heading">
              <div>
                <p className="eyebrow">PROJECTS</p>
                <h1>把一道招牌菜，讲成一个好故事</h1>
                <p>用引导式问卷快速生成可直接开拍的竖屏脚本。</p>
              </div>
              <button className="primary-button" type="button" onClick={startNewProject}>
                ＋ 新建项目
              </button>
            </div>

            {busy && projects.length === 0 ? (
              <div className="empty-state">正在加载项目…</div>
            ) : projects.length === 0 ? (
              <div className="empty-state">
                <span>🎬</span>
                <h2>还没有脚本项目</h2>
                <p>从老板信息和招牌菜开始，几分钟完成第一版脚本。</p>
                <button className="primary-button" type="button" onClick={startNewProject}>
                  创建第一个项目
                </button>
              </div>
            ) : (
              <div className="project-grid">
                {projects.map((item) => (
                  <button
                    className="project-card"
                    type="button"
                    key={item.id}
                    onClick={() => void openProject(item)}
                  >
                    <span className={`status-dot ${item.script ? 'ready' : ''}`} />
                    <h2>{item.name}</h2>
                    <p>{item.boss_info.cuisine_type ?? '待完善问卷'}</p>
                    <footer>
                      <span>{item.script ? `${item.script.shots.length} 个镜头` : '尚未生成'}</span>
                      <time>{formatDate(item.created_at)}</time>
                    </footer>
                  </button>
                ))}
              </div>
            )}
          </section>
        )}

        {view === 'setup' && (
          <section className="setup-layout">
            <aside className="setup-intro">
              <p className="eyebrow">SCRIPT BRIEF</p>
              <h1>先认识你的店</h1>
              <p>这些信息会被直接填进镜头台词。写得越具体，脚本越像老板本人。</p>
              <ol>
                <li className="active">门店与菜品</li>
                <li>老板与顾客</li>
                <li>视频表达</li>
              </ol>
              {project && !script && (
                <div className="empty-hint">
                  <strong>这个项目还没有脚本</strong>
                  <span>完成右侧问卷后即可生成第一版，生成结果可以继续手工修改。</span>
                </div>
              )}
            </aside>
            <form className="setup-form" onSubmit={(event) => void submitSetup(event)}>
              <div className="form-section">
                <span className="step-number">01</span>
                <div>
                  <h2>门店与菜品</h2>
                  <p>告诉观众，你是谁、最值得吃什么。</p>
                </div>
              </div>
              <label>
                店名
                <input
                  required
                  value={form.restaurant_name}
                  onChange={(event) =>
                    setForm({ ...form, restaurant_name: event.target.value })
                  }
                  placeholder="例如：阿芳家常菜"
                />
              </label>
              <div className="two-columns">
                <label>
                  菜系
                  <select
                    value={form.cuisine_type}
                    onChange={(event) => setForm({ ...form, cuisine_type: event.target.value })}
                  >
                    <option>家常菜</option>
                    <option>川菜</option>
                    <option>火锅</option>
                    <option>烧烤</option>
                    <option>其他</option>
                  </select>
                </label>
                <label>
                  招牌菜
                  <input
                    required
                    value={dishText}
                    onChange={(event) => setDishText(event.target.value)}
                    placeholder="多道菜用顿号隔开"
                  />
                </label>
              </div>

              <div className="form-section divider">
                <span className="step-number">02</span>
                <div>
                  <h2>老板与顾客</h2>
                  <p>让口播有人物感，也清楚说给谁听。</p>
                </div>
              </div>
              <label>
                老板人设 / 口播风格
                <textarea
                  value={form.owner_persona}
                  onChange={(event) => setForm({ ...form, owner_persona: event.target.value })}
                  placeholder="例如：爽快、爱开玩笑，坚持每天亲自选菜"
                  rows={3}
                />
              </label>
              <label>
                目标人群
                <input
                  value={form.audience}
                  onChange={(event) => setForm({ ...form, audience: event.target.value })}
                  placeholder="例如：附近上班族、周末家庭聚餐"
                />
              </label>

              <div className="form-section divider">
                <span className="step-number">03</span>
                <div>
                  <h2>视频表达</h2>
                  <p>选好平台和节奏，镜头时长会自动适配。</p>
                </div>
              </div>
              <div className="three-columns">
                <label>
                  发布平台
                  <select
                    value={form.platform}
                    onChange={(event) => setForm({ ...form, platform: event.target.value })}
                  >
                    <option>抖音</option>
                    <option>视频号</option>
                    <option>小红书</option>
                    <option>快手</option>
                  </select>
                </label>
                <label>
                  视频风格
                  <select
                    value={form.video_style}
                    onChange={(event) => setForm({ ...form, video_style: event.target.value })}
                  >
                    <option>竖屏口播</option>
                    <option>探店纪实</option>
                    <option>后厨展示</option>
                  </select>
                </label>
                <label>
                  目标时长（秒）
                  <input
                    type="number"
                    min={15}
                    max={180}
                    value={form.target_duration_seconds}
                    onChange={(event) =>
                      setForm({ ...form, target_duration_seconds: Number(event.target.value) })
                    }
                  />
                </label>
              </div>
              <label>
                开头偏好（可选）
                <input
                  value={form.hook_preference}
                  onChange={(event) => setForm({ ...form, hook_preference: event.target.value })}
                  placeholder="例如：先问一句“你吃过会爆汁的红烧肉吗？”"
                />
              </label>
              <div className="form-actions">
                <span>纯模板生成 · 无模型费用 · 结果可编辑</span>
                <button className="primary-button" type="submit" disabled={busy}>
                  {busy ? '正在生成…' : '生成脚本 →'}
                </button>
              </div>
            </form>
          </section>
        )}

        {view === 'script' && script && (
          <section className="script-page">
            <div className="script-toolbar">
              <div>
                <p className="eyebrow">SHOOTING SCRIPT</p>
                <h1>{project?.name}</h1>
                <p>{script.shots.length} 个镜头 · 目标 {script.target_duration_seconds} 秒 · {script.style}</p>
              </div>
              <div className="toolbar-actions">
                <button className="ghost-button" type="button" onClick={() => setView('setup')}>
                  修改问卷
                </button>
                <button className="ghost-button" type="button" onClick={() => void openMaterials()}>
                  管理素材
                </button>
                <button
                  className="primary-button"
                  type="button"
                  disabled={busy}
                  onClick={() => void saveCurrentScript()}
                >
                  {busy ? '保存中…' : '保存修改'}
                </button>
              </div>
            </div>

            <div className="script-summary">
              <label>
                脚本标题
                <input
                  value={script.title}
                  onChange={(event) => setScript({ ...script, title: event.target.value })}
                />
              </label>
              <label>
                开场钩子
                <textarea
                  rows={2}
                  value={script.opening_hook}
                  onChange={(event) =>
                    setScript({ ...script, opening_hook: event.target.value })
                  }
                />
              </label>
              <label>
                行动引导 CTA
                <textarea
                  rows={2}
                  value={script.cta}
                  onChange={(event) => setScript({ ...script, cta: event.target.value })}
                />
              </label>
            </div>

            {script.shots.length === 0 ? (
              <div className="empty-state compact">
                <span>📝</span>
                <h2>脚本里还没有镜头</h2>
                <p>返回问卷重新生成，或补充镜头后再保存。</p>
              </div>
            ) : (
              <div className="shot-list">
                {script.shots.map((shot, index) => (
                <article className="shot-card" key={shot.shot_index}>
                  <header>
                    <span className="shot-index">{String(shot.shot_index).padStart(2, '0')}</span>
                    <div>
                      <strong>{shot.location || `镜头 ${shot.shot_index}`}</strong>
                      <small>{shot.angle}</small>
                    </div>
                    <label className="duration-field">
                      <input
                        aria-label={`镜头 ${shot.shot_index} 时长`}
                        type="number"
                        min={0}
                        value={shot.duration_hint_seconds}
                        onChange={(event) =>
                          updateShot(index, 'duration_hint_seconds', Number(event.target.value))
                        }
                      />
                      秒
                    </label>
                  </header>
                  <label>
                    台词
                    <textarea
                      rows={3}
                      value={shot.lines}
                      onChange={(event) => updateShot(index, 'lines', event.target.value)}
                    />
                  </label>
                  <label>
                    拍摄要点
                    <textarea
                      rows={3}
                      value={shot.shooting_tips}
                      onChange={(event) =>
                        updateShot(index, 'shooting_tips', event.target.value)
                      }
                    />
                  </label>
                </article>
                ))}
              </div>
            )}
          </section>
        )}

        {view === 'materials' && project && (
          <section className="materials-page">
            <div className="script-toolbar">
              <div>
                <p className="eyebrow">MATERIALS & TIMELINE</p>
                <h1>{project.name}</h1>
                <p>按拍摄顺序上传镜头，时长与位置由后端时间轴统一计算。</p>
              </div>
              <div className="toolbar-actions">
                {script && (
                  <button className="ghost-button" type="button" onClick={() => setView('script')}>
                    返回脚本
                  </button>
                )}
                {materials.length >= 2 && (
                  <button className="primary-button" type="button" onClick={() => void openEdit()}>
                    继续缝合 →
                  </button>
                )}
                <label className={`upload-button ${busy ? 'disabled' : ''}`}>
                  {busy ? '处理中…' : '＋ 上传视频'}
                  <input
                    type="file"
                    multiple
                    accept=".mp4,.mov,.mkv,.avi"
                    disabled={busy}
                    onChange={(event) => {
                      void uploadSelectedFiles(event.target.files)
                      event.target.value = ''
                    }}
                  />
                </label>
              </div>
            </div>

            <div className="timeline-panel">
              <div className="timeline-heading">
                <div>
                  <strong>成片时间轴</strong>
                  <small>每段宽度按后端返回的可用时长展示</small>
                </div>
                <span>{(timeline?.total_duration ?? 0).toFixed(2)} 秒</span>
              </div>
              {timeline?.segments.length ? (
                <div className="timeline-track">
                  {timeline.segments.map((segment) => (
                    <div
                      className="timeline-segment"
                      key={segment.shot_index}
                      style={{ flexGrow: segment.used_duration }}
                      title={`镜头 ${segment.shot_index}：${segment.used_duration.toFixed(2)} 秒`}
                    >
                      <span>{segment.shot_index}</span>
                      <small>{segment.used_duration.toFixed(1)}s</small>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="timeline-empty">上传素材后，这里会显示权威时间轴。</div>
              )}
            </div>

            {materials.length === 0 ? (
              <div className="empty-state compact">
                <span>📹</span>
                <h2>还没有拍摄素材</h2>
                <p>可一次选择多个视频，系统会按选择顺序依次上传。</p>
              </div>
            ) : (
              <div className="material-grid">
                {materials.map((material) => (
                  <article className="material-card" key={material.shot_index}>
                    <img
                      src={getThumbnailUrl(project.id, material.shot_index)}
                      alt={`镜头 ${material.shot_index} 缩略图`}
                    />
                    <div>
                      <span className="material-index">镜头 {material.shot_index}</span>
                      <strong>{material.filename}</strong>
                      <small>
                        {material.duration.toFixed(2)} 秒 · {material.width}×{material.height}
                      </small>
                    </div>
                    <button
                      className="delete-button"
                      type="button"
                      disabled={busy}
                      onClick={() => void removeMaterial(material.shot_index)}
                    >
                      删除
                    </button>
                  </article>
                ))}
              </div>
            )}
          </section>
        )}

        {view === 'edit' && project && edits && timeline && (
          <section className="edit-page">
            <div className="script-toolbar">
              <div>
                <p className="eyebrow">JUNCTION EDITOR</p>
                <h1>调好每一道接缝</h1>
                <p>点选片段间的接缝，微调前后裁剪和转场。</p>
              </div>
              <div className="toolbar-actions">
                <button className="ghost-button" type="button" onClick={() => setView('materials')}>
                  返回素材
                </button>
                <button className="primary-button" type="button" onClick={openExport}>
                  继续导出 →
                </button>
              </div>
            </div>

            <div className="timeline-panel edit-timeline">
              <div className="timeline-heading">
                <div>
                  <strong>接缝时间轴</strong>
                  <small>片段宽度严格按后端 used_duration 比例显示</small>
                </div>
                <span>{timeline.total_duration.toFixed(2)} 秒</span>
              </div>
              <div className="timeline-track junction-track">
                {timeline.segments.map((segment, index) => (
                  <div className="timeline-piece" key={segment.shot_index}>
                    <div
                      className="timeline-segment"
                      style={{ flexGrow: segment.used_duration }}
                      title={`镜头 ${segment.shot_index}：${segment.used_duration.toFixed(2)} 秒`}
                    >
                      <span>{segment.shot_index}</span>
                      <small>{segment.used_duration.toFixed(1)}s</small>
                    </div>
                    {index < timeline.segments.length - 1 && (
                      <button
                        className={`junction-button ${selectedJunction === index ? 'selected' : ''}`}
                        type="button"
                        aria-label={`编辑接缝 ${index}`}
                        onClick={() => {
                          setSelectedJunction(index)
                          setPreviewVersion(Date.now())
                        }}
                      >
                        ✦
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {edits.junctions[selectedJunction] && (
              <div className="junction-panel">
                <div className="preview-player">
                  <video
                    key={`${selectedJunction}-${previewVersion}`}
                    controls
                    playsInline
                    src={`${previewJunctionUrl(project.id, selectedJunction, 1.5, 1.5)}&t=${previewVersion}`}
                  />
                  <small>接缝 {selectedJunction + 1} · 前后各预览最多 1.5 秒</small>
                </div>

                <div className="junction-controls">
                  <div className="trim-control">
                    <span>剪前段尾部</span>
                    <strong>{edits.shots[selectedJunction].trim_tail.toFixed(2)}s</strong>
                    <div className="step-buttons">
                      {[-0.2, -0.1, 0.1, 0.2].map((delta) => (
                        <button
                          type="button"
                          disabled={junctionBusy}
                          key={delta}
                          onClick={() =>
                            void saveJunction({
                              trimTail: edits.shots[selectedJunction].trim_tail + delta,
                            })
                          }
                        >
                          {delta > 0 ? '+' : '−'}{Math.abs(delta).toFixed(1)}s
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="trim-control">
                    <span>剪后段头部</span>
                    <strong>{edits.shots[selectedJunction + 1].trim_head.toFixed(2)}s</strong>
                    <div className="step-buttons">
                      {[-0.2, -0.1, 0.1, 0.2].map((delta) => (
                        <button
                          type="button"
                          disabled={junctionBusy}
                          key={delta}
                          onClick={() =>
                            void saveJunction({
                              trimHead: edits.shots[selectedJunction + 1].trim_head + delta,
                            })
                          }
                        >
                          {delta > 0 ? '+' : '−'}{Math.abs(delta).toFixed(1)}s
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="transition-control">
                    <label>
                      转场
                      <select
                        disabled={junctionBusy}
                        value={edits.junctions[selectedJunction].transition === 'hard' ? 'hard' : 'fade'}
                        onChange={(event) =>
                          void saveJunction({}, { transition: event.target.value as 'hard' | 'fade' })
                        }
                      >
                        <option value="hard">硬切</option>
                        <option value="fade">淡入淡出</option>
                      </select>
                    </label>
                    <div className="fade-stepper">
                      <span>Fade 时长</span>
                      <button
                        type="button"
                        disabled={junctionBusy || edits.junctions[selectedJunction].transition === 'hard'}
                        onClick={() =>
                          void saveJunction({}, {
                            fade_seconds: edits.junctions[selectedJunction].fade_seconds - 0.1,
                          })
                        }
                      >
                        −0.1s
                      </button>
                      <strong>{edits.junctions[selectedJunction].fade_seconds.toFixed(2)}s</strong>
                      <button
                        type="button"
                        disabled={junctionBusy || edits.junctions[selectedJunction].transition === 'hard'}
                        onClick={() =>
                          void saveJunction({}, {
                            fade_seconds: edits.junctions[selectedJunction].fade_seconds + 0.1,
                          })
                        }
                      >
                        +0.1s
                      </button>
                    </div>
                  </div>
                </div>

                <footer className="junction-summary">
                  <span>{junctionBusy ? '正在保存并刷新预览…' : '所有数值由服务端自动钳制'}</span>
                  <strong>预计总时长：{timeline.total_duration.toFixed(2)} 秒</strong>
                </footer>
              </div>
            )}
          </section>
        )}

        {view === 'export' && project && (
          <section className="export-page">
            <div className="script-toolbar">
              <div>
                <p className="eyebrow">FINAL EXPORT</p>
                <h1>导出竖屏成片</h1>
                <p>1080×1920 · 30 fps · H.264 + AAC</p>
              </div>
              <button className="ghost-button" type="button" onClick={() => setView('edit')}>
                返回接缝编辑
              </button>
            </div>

            <div className="export-card">
              <div className="export-icon">↗</div>
              <h2>{exportJob?.status === 'done' ? '成片已就绪' : '准备生成最终视频'}</h2>
              <p>{exportJob?.message ?? `预计总时长 ${(timeline?.total_duration ?? 0).toFixed(2)} 秒`}</p>

              {exportJob?.status === 'failed' && (
                <div className="notice error export-error" role="alert">
                  导出失败：{exportJob.message || '请检查素材后重试'}
                </div>
              )}

              {exportJob && (
                <div className="progress-block">
                  <div className="progress-label">
                    <span>渲染进度</span>
                    <strong>{Math.round(exportJob.progress)}%</strong>
                  </div>
                  <div className="progress-track" role="progressbar" aria-valuenow={exportJob.progress}>
                    <div style={{ width: `${Math.min(100, Math.max(0, exportJob.progress))}%` }} />
                  </div>
                </div>
              )}

              {exportJob?.status === 'done' ? (
                <a className="download-button" href={exportUrl(project.id)} download="final.mp4">
                  下载 final.mp4
                </a>
              ) : (
                <button
                  className="primary-button export-button"
                  type="button"
                  disabled={busy || exportJob?.status === 'pending' || exportJob?.status === 'running'}
                  onClick={() => void beginExport()}
                >
                  {exportJob?.status === 'failed' ? '重新导出' : '导出成片'}
                </button>
              )}
            </div>
          </section>
        )}
      </main>
    </div>
  )
}
