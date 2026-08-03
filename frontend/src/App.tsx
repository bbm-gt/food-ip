import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'

import {
  createProject,
  deleteMaterial,
  generateScriptBundle,
  getEdits,
  getJob,
  getProject,
  getProjects,
  getTimeline,
  listMaterials,
  putResearch,
  putJunction,
  putScript,
  selectScriptCandidate,
  startExport,
  uploadMaterial,
} from './api/client'
import type { Edits, ExportJob, JunctionEdit, Material, Project, ResearchProfile, ScriptBundle, ScriptCandidate, ScriptModel, Shot, Timeline } from './api/types'
import { EditView, ExportView, MaterialsView } from './views/ProductionViews'
import { CandidatesView, ProjectsView, ScriptView, SetupView } from './views/ProjectViews'
import './app.css'

type View = 'list' | 'setup' | 'candidates' | 'script' | 'materials' | 'edit' | 'export'

const EMPTY_RESEARCH: ResearchProfile = {
  schema_version: 1,
  store: {
    restaurant_name: '', city: '', business_district: '', cuisine_type: '家常菜',
    years_in_business: 0, price_per_person: 0, signature_dishes: [], business_modes: [],
    differentiators: [], ingredient_proofs: [], visible_processes: [],
    customer_praises: [], customer_misunderstandings: [],
  },
  owner: {
    owner_name: '', hometown: '', owner_persona: '', origin_story: '', hardest_moment: '',
    proudest_moment: '', unique_experience: '', speaking_style: '实在真诚',
    appearance_mode: '真人口播', language_style: '普通话', avoided_topics: [],
    allow_personal_story: false,
  },
  audience: {
    core_audience: '', dining_scenarios: [], customer_needs: [], customer_concerns: [],
    current_business_problem: '', content_goal: '吸引到店',
  },
  shooting: {
    platform: '抖音', video_style: '烟火气纪实', target_duration_seconds: 60,
    available_locations: [], unavailable_locations: [], can_show_kitchen: true,
    can_show_customers: false, equipment: [], daily_minutes: 20,
    update_frequency: '每周3条', hook_preference: '',
  },
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
  const [research, setResearch] = useState<ResearchProfile>(EMPTY_RESEARCH)
  const [bundle, setBundle] = useState<ScriptBundle | null>(null)
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
    setProject(null); setResearch(EMPTY_RESEARCH); setBundle(null); setScript(null)
    setMaterials([]); setEdits(null); setTimeline(null); setExportJobId(null)
    setExportJob(null); setMessage(''); setError(''); setView('setup')
  }

  async function openProject(item: Project) {
    setBusy(true); setError('')
    try {
      const fullProject = await getProject(item.id)
      setProject(fullProject); setResearch(fullProject.research ?? EMPTY_RESEARCH)
      setBundle(fullProject.script_bundle); setScript(fullProject.script)
      setView(fullProject.script ? 'script' : fullProject.script_bundle ? 'candidates' : 'setup')
    } catch (reason) { setError(reason instanceof Error ? reason.message : '项目打开失败') }
    finally { setBusy(false) }
  }

  async function submitSetup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(''); setMessage('')
    try {
      const activeProject = project ?? await createProject(research.store.restaurant_name || '未命名餐饮项目')
      if (!project) setProject(activeProject)
      const savedResearch = await putResearch(activeProject.id, research)
      const generated = await generateScriptBundle(activeProject.id, savedResearch)
      const updatedProject = { ...activeProject, research: savedResearch, script_bundle: generated }
      setProject(updatedProject); setResearch(savedResearch); setBundle(generated)
      setView('candidates'); setMessage(`已由 ${generated.model_name || 'AI'} 生成并通过结构校验。`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '脚本方案生成失败') }
    finally { setBusy(false) }
  }

  async function regenerateBundle() {
    if (!project) return
    setBusy(true); setError(''); setMessage('')
    try {
      const generated = await generateScriptBundle(project.id, research)
      setBundle(generated); setProject({ ...project, research, script_bundle: generated })
      setMessage(`已由 ${generated.model_name || 'AI'} 重新生成三套方案。`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '重新生成失败') }
    finally { setBusy(false) }
  }

  async function chooseCandidate(candidate: ScriptCandidate) {
    if (!project || !bundle) return
    setBusy(true); setError(''); setMessage('')
    try {
      const selected = await selectScriptCandidate(project.id, bundle.id, candidate.id)
      const selectedBundle = { ...bundle, selected_script_id: candidate.id }
      setScript(selected); setBundle(selectedBundle)
      setProject({ ...project, script: selected, script_bundle: selectedBundle })
      setView('script'); setMessage(`已选择“${candidate.strategy_name}”，可以继续修改或开始拍摄。`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '选择脚本失败') }
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
      {view === 'setup' && <SetupView project={project} script={script} research={research} busy={busy} onResearchChange={setResearch} onSubmit={(event) => void submitSetup(event)} />}
      {view === 'candidates' && project && bundle && <CandidatesView project={project} bundle={bundle} busy={busy} onSelect={(candidate) => void chooseCandidate(candidate)} onRegenerate={() => void regenerateBundle()} onSetup={() => setView('setup')} />}
      {view === 'script' && script && <ScriptView project={project} script={script} busy={busy} hasAlternatives={Boolean(bundle)} onScriptChange={setScript} onUpdateShot={updateShot} onSetup={() => setView('setup')} onCandidates={() => setView('candidates')} onMaterials={() => void openMaterials()} onSave={() => void saveCurrentScript()} />}
      {view === 'materials' && project && <MaterialsView project={project} script={script} materials={materials} timeline={timeline} busy={busy} canEdit={hasContiguousMaterials(materials)} onScript={() => setView('script')} onEdit={() => void openEdit()} onUpload={(files) => void uploadSelectedFiles(files)} onDelete={(index) => void removeMaterial(index)} />}
      {view === 'edit' && project && edits && timeline && <EditView project={project} edits={edits} timeline={timeline} selectedJunction={selectedJunction} previewVersion={previewVersion} junctionBusy={junctionBusy} onSelectJunction={(index) => { setSelectedJunction(index); setPreviewVersion(Date.now()) }} onSaveJunction={(shotPatch, junctionPatch) => void saveJunction(shotPatch, junctionPatch)} onMaterials={() => setView('materials')} onExport={() => { setError(''); setMessage(''); setView('export') }} />}
      {view === 'export' && project && <ExportView project={project} timeline={timeline} job={exportJob} busy={busy} onEdit={() => setView('edit')} onExport={() => void beginExport()} />}
    </main>
  </div>
}
