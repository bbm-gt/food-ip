import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'

import {
  createProject,
  deleteMaterial,
  generateTemplate,
  getEdits,
  getJob,
  getProject,
  getProjects,
  getTimeline,
  listMaterials,
  patchProject,
  putJunction,
  putScript,
  startExport,
  uploadMaterial,
} from './api/client'
import type { BossInfo, Edits, ExportJob, JunctionEdit, Material, Project, ScriptModel, Shot, Timeline } from './api/types'
import { EditView, ExportView, MaterialsView } from './views/ProductionViews'
import { ProjectsView, ScriptView, SetupView } from './views/ProjectViews'
import './app.css'

type View = 'list' | 'setup' | 'script' | 'materials' | 'edit' | 'export'

const EMPTY_FORM: BossInfo = {
  restaurant_name: '', cuisine_type: '家常菜', signature_dishes: [], owner_persona: '',
  audience: '', video_style: '竖屏口播', target_duration_seconds: 60,
  platform: '抖音', hook_preference: '',
}

function nextUploadIndexes(count: number, materials: Material[], script: ScriptModel | null): number[] {
  const used = new Set(materials.map((material) => material.shot_index))
  const preferred = (script?.shots ?? [])
    .map((shot) => shot.shot_index)
    .filter((index) => Number.isInteger(index) && index > 0)
  const result: number[] = []

  for (const index of preferred) {
    if (result.length >= count) break
    if (!used.has(index)) {
      used.add(index)
      result.push(index)
    }
  }
  let fallback = 1
  while (result.length < count) {
    while (used.has(fallback)) fallback += 1
    used.add(fallback)
    result.push(fallback)
  }
  return result
}

function hasContiguousMaterials(materials: Material[]) {
  return materials.length >= 2 && materials.every((item, index) => (
    index === 0 || item.shot_index === materials[index - 1].shot_index + 1
  ))
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
    setBusy(true); setError('')
    try { setProjects(await getProjects()) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '项目加载失败') }
    finally { setBusy(false) }
  }

  useEffect(() => { void loadProjects() }, [])
  useEffect(() => () => {
    if (previewTimer.current !== null) window.clearTimeout(previewTimer.current)
  }, [])
  useEffect(() => {
    if (!exportJobId || exportJob?.status === 'done' || exportJob?.status === 'failed') return
    let cancelled = false
    async function pollJob() {
      try { const job = await getJob(exportJobId as string); if (!cancelled) setExportJob(job) }
      catch (reason) { if (!cancelled) setError(reason instanceof Error ? reason.message : '导出进度查询失败') }
    }
    void pollJob()
    const timer = window.setInterval(() => void pollJob(), 1000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [exportJobId, exportJob?.status])

  function startNewProject() {
    setProject(null); setForm(EMPTY_FORM); setDishText(''); setScript(null)
    setMaterials([]); setEdits(null); setTimeline(null); setExportJobId(null)
    setExportJob(null); setMessage(''); setError(''); setView('setup')
  }

  async function openProject(item: Project) {
    setBusy(true); setError('')
    try {
      const fullProject = await getProject(item.id)
      setProject(fullProject); setForm({ ...EMPTY_FORM, ...fullProject.boss_info })
      setDishText(fullProject.boss_info.signature_dishes?.join('、') ?? '')
      setScript(fullProject.script); setView(fullProject.script ? 'script' : 'setup')
    } catch (reason) { setError(reason instanceof Error ? reason.message : '项目打开失败') }
    finally { setBusy(false) }
  }

  async function submitSetup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(''); setMessage('')
    const bossInfo = { ...form, signature_dishes: dishText.split(/[、,，]/).map((dish) => dish.trim()).filter(Boolean) }
    try {
      const activeProject = project ?? await createProject(bossInfo.restaurant_name || '未命名餐饮项目')
      await patchProject(activeProject.id, bossInfo)
      const generated = await generateTemplate(activeProject.id, bossInfo)
      setProject({ ...activeProject, boss_info: bossInfo, script: generated })
      setForm(bossInfo); setScript(generated); setView('script'); setMessage('脚本已生成并保存。')
    } catch (reason) { setError(reason instanceof Error ? reason.message : '脚本生成失败') }
    finally { setBusy(false) }
  }

  async function saveCurrentScript() {
    if (!project || !script) return
    setBusy(true); setError(''); setMessage('')
    try { const saved = await putScript(project.id, script); setScript(saved); setProject({ ...project, script: saved }); setMessage('修改已保存。') }
    catch (reason) { setError(reason instanceof Error ? reason.message : '脚本保存失败') }
    finally { setBusy(false) }
  }

  async function loadMaterials(projectId: string) {
    const [loadedMaterials, loadedTimeline] = await Promise.all([listMaterials(projectId), getTimeline(projectId)])
    setMaterials(loadedMaterials); setTimeline(loadedTimeline)
  }

  async function openMaterials() {
    if (!project) return
    setBusy(true); setError(''); setMessage(''); setView('materials')
    try { await loadMaterials(project.id) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '素材加载失败') }
    finally { setBusy(false) }
  }

  async function openEdit() {
    if (!project) return
    setBusy(true); setError(''); setMessage('')
    try {
      const [loadedMaterials, loadedEdits, loadedTimeline] = await Promise.all([listMaterials(project.id), getEdits(project.id), getTimeline(project.id)])
      if (!hasContiguousMaterials(loadedMaterials)) throw new Error('镜头编号不连续，请先补齐缺失素材')
      setMaterials(loadedMaterials); setEdits(loadedEdits); setTimeline(loadedTimeline)
      setSelectedJunction(Math.min(selectedJunction, Math.max(0, loadedEdits.junctions.length - 1)))
      setPreviewVersion(Date.now()); setView('edit')
    } catch (reason) { setError(reason instanceof Error ? reason.message : '接缝编辑加载失败') }
    finally { setBusy(false) }
  }

  function schedulePreviewRefresh() {
    if (previewTimer.current !== null) window.clearTimeout(previewTimer.current)
    previewTimer.current = window.setTimeout(() => { setPreviewVersion(Date.now()); previewTimer.current = null }, 400)
  }

  async function saveJunction(shotPatch: { trimTail?: number; trimHead?: number }, junctionPatch: Partial<JunctionEdit> = {}) {
    if (!project || !edits) return
    const left = edits.shots[selectedJunction]; const right = edits.shots[selectedJunction + 1]
    const junction = edits.junctions[selectedJunction]
    if (!left || !right || !junction) return
    setJunctionBusy(true); setError('')
    try {
      const response = await putJunction(project.id, selectedJunction, {
        trim_tail: shotPatch.trimTail ?? left.trim_tail,
        trim_head: shotPatch.trimHead ?? right.trim_head,
        transition: junctionPatch.transition ?? junction.transition,
        fade_seconds: junctionPatch.fade_seconds ?? junction.fade_seconds,
      })
      setEdits(response.edits); setTimeline(response.timeline); schedulePreviewRefresh()
    } catch (reason) { setError(reason instanceof Error ? reason.message : '接缝更新失败') }
    finally { setJunctionBusy(false) }
  }

  async function beginExport() {
    if (!project) return
    setBusy(true); setError(''); setMessage(''); setExportJob(null)
    try { const result = await startExport(project.id); setExportJobId(result.job_id); setExportJob({ status: 'pending', progress: 0, message: '等待导出', result: null }) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '导出启动失败') }
    finally { setBusy(false) }
  }

  async function uploadSelectedFiles(files: FileList | null) {
    if (!project || !files?.length) return
    setBusy(true); setError(''); setMessage('')
    try {
      const indexes = nextUploadIndexes(files.length, materials, script)
      for (const [position, file] of Array.from(files).entries()) {
        const formData = new FormData(); formData.append('shot_index', String(indexes[position])); formData.append('file', file)
        await uploadMaterial(project.id, formData)
      }
      await loadMaterials(project.id); setMessage(`已上传 ${files.length} 个素材。`)
    } catch (reason) { await loadMaterials(project.id).catch(() => undefined); setError(reason instanceof Error ? reason.message : '素材上传失败') }
    finally { setBusy(false) }
  }

  async function removeMaterial(shotIndex: number) {
    if (!project) return
    setBusy(true); setError(''); setMessage('')
    try { await deleteMaterial(project.id, shotIndex); await loadMaterials(project.id); setMessage(`素材 ${shotIndex} 已删除，下次上传会优先补齐该镜头。`) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '素材删除失败') }
    finally { setBusy(false) }
  }

  function updateShot(index: number, field: keyof Shot, value: string | number) {
    setScript((current) => current ? { ...current, shots: current.shots.map((shot, shotIndex) => shotIndex === index ? { ...shot, [field]: value } : shot) } : current)
  }

  async function backToProjects() { setView('list'); setMessage(''); setError(''); await loadProjects() }

  return <div className="app-shell">
    <header className="topbar"><button className="brand" type="button" onClick={() => void backToProjects()}><span className="brand-mark">食</span><span><strong>Food IP Studio</strong><small>餐饮短视频脚本工坊</small></span></button>{view !== 'list' && <button className="ghost-button" type="button" onClick={() => void backToProjects()}>返回项目</button>}</header>
    <main className="page">
      {busy && <div className="loading-indicator" role="status" aria-live="polite">正在处理，请稍候…</div>}
      {error && <div className="notice error" role="alert">{error}</div>}
      {message && <div className="notice success" role="status">{message}</div>}
      {view === 'list' && <ProjectsView busy={busy} projects={projects} onNew={startNewProject} onOpen={(item) => void openProject(item)} />}
      {view === 'setup' && <SetupView project={project} script={script} form={form} dishText={dishText} busy={busy} onFormChange={setForm} onDishTextChange={setDishText} onSubmit={(event) => void submitSetup(event)} />}
      {view === 'script' && script && <ScriptView project={project} script={script} busy={busy} onScriptChange={setScript} onUpdateShot={updateShot} onSetup={() => setView('setup')} onMaterials={() => void openMaterials()} onSave={() => void saveCurrentScript()} />}
      {view === 'materials' && project && <MaterialsView project={project} script={script} materials={materials} timeline={timeline} busy={busy} canEdit={hasContiguousMaterials(materials)} onScript={() => setView('script')} onEdit={() => void openEdit()} onUpload={(files) => void uploadSelectedFiles(files)} onDelete={(index) => void removeMaterial(index)} />}
      {view === 'edit' && project && edits && timeline && <EditView project={project} edits={edits} timeline={timeline} selectedJunction={selectedJunction} previewVersion={previewVersion} junctionBusy={junctionBusy} onSelectJunction={(index) => { setSelectedJunction(index); setPreviewVersion(Date.now()) }} onSaveJunction={(shotPatch, junctionPatch) => void saveJunction(shotPatch, junctionPatch)} onMaterials={() => setView('materials')} onExport={() => { setError(''); setMessage(''); setView('export') }} />}
      {view === 'export' && project && <ExportView project={project} timeline={timeline} job={exportJob} busy={busy} onEdit={() => setView('edit')} onExport={() => void beginExport()} />}
    </main>
  </div>
}
