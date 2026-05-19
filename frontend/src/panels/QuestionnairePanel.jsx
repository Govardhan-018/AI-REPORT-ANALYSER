import { useState, useRef, useEffect } from 'react'
import './QuestionnairePanel.css'

const API = 'http://localhost:8000'

export default function QuestionnairePanel({ chatReady }) {
  const [step, setStep] = useState(1)
  const [uploadResponse, setUploadResponse] = useState(null)
  const [columnMapping, setColumnMapping] = useState({
    question_column: '', answer_column: '', status_column: '',
    evidence_column: '', page_column: '', confidence_column: ''
  })
  const [progress, setProgress] = useState({ current: 0, total: 0, question: '' })
  const [results, setResults] = useState([])
  const [downloadToken, setDownloadToken] = useState(null)
  const [summary, setSummary] = useState(null)
  const [isProcessing, setIsProcessing] = useState(false)
  const [isDragging, setIsDragging] = useState(false)
  const [expandedRows, setExpandedRows] = useState({})
  const [error, setError] = useState(null)
  const [modelsData, setModelsData] = useState(null)
  const [selectedModel, setSelectedModel] = useState({ provider: 'ollama', model: null })
  const fileInputRef = useRef(null)

  // Fetch available models on mount
  useEffect(() => {
    const fetchModels = async () => {
      try {
        const res = await fetch(`${API}/api/models`)
        if (res.ok) {
          const data = await res.json()
          setModelsData(data.providers)
          // Auto-select best available provider
          if (data.providers.ollama?.available && data.providers.ollama.models.length > 0) {
            setSelectedModel({ provider: 'ollama', model: data.providers.ollama.models.find(m => !m.includes('embed')) || data.providers.ollama.models[0] })
          } else if (data.providers.groq?.available) {
            setSelectedModel({ provider: 'groq', model: data.providers.groq.models[0] })
          }
        }
      } catch { }
    }
    fetchModels()
  }, [])

  // ─── STEP 1: Upload ───
  const uploadFile = async (file) => {
    if (!file.name.endsWith('.xlsx')) { setError('Only .xlsx files are supported.'); return }
    setError(null)
    const formData = new FormData()
    formData.append('file', file)
    try {
      const res = await fetch(`${API}/questionnaire/upload`, { method: 'POST', body: formData })
      if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || 'Upload failed') }
      const data = await res.json()
      setUploadResponse(data)
      // Auto-guess question column
      const guess = data.detected_columns.find(c =>
        /question|requirement|control|description/i.test(c)
      ) || data.detected_columns[0]
      setColumnMapping(prev => ({ ...prev, question_column: guess }))
    } catch (e) { setError(e.message) }
  }

  // ─── STEP 3: Run Processing ───
  const runAutoFill = async () => {
    if (!uploadResponse || !columnMapping.question_column) return
    setStep(3); setIsProcessing(true); setResults([]); setError(null)
    setSummary(null); setDownloadToken(null)
    setProgress({ current: 0, total: uploadResponse.total_rows, question: '' })

    try {
      const res = await fetch(`${API}/questionnaire/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          file_id: uploadResponse.file_id,
          ...columnMapping,
          provider: selectedModel.provider,
          model: selectedModel.model || undefined,
        })
      })
      if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || 'Run failed') }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let currentEvent = ''

      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()

        for (const line of lines) {
          if (line.startsWith('event:')) { currentEvent = line.substring(6).trim() }
          else if (line.startsWith('data:') && currentEvent) {
            try {
              const payload = JSON.parse(line.substring(5).trim())
              if (currentEvent === 'progress') setProgress({ current: payload.current, total: payload.total, question: payload.question })
              else if (currentEvent === 'row_done') setResults(prev => [...prev, payload])
              else if (currentEvent === 'complete') { setDownloadToken(payload.download_token); setSummary(payload.summary) }
              else if (currentEvent === 'error' && payload.row >= 0) setResults(prev => [...prev, { row_index: payload.row, question: '', answer: `Error: ${payload.error}`, status: 'No Evidence', confidence_score: 0 }])
            } catch { }
            currentEvent = ''
          }
        }
      }
    } catch (e) { setError(e.message) }
    setIsProcessing(false)
  }

  const startOver = () => {
    setStep(1); setUploadResponse(null); setResults([]); setDownloadToken(null)
    setSummary(null); setIsProcessing(false); setError(null)
    setColumnMapping({ question_column: '', answer_column: '', status_column: '', evidence_column: '', page_column: '', confidence_column: '' })
  }

  // Model selector label for display
  const modelLabel = selectedModel.model
    ? `${selectedModel.provider === 'ollama' ? '🖥' : selectedModel.provider === 'groq' ? '⚡' : '☁️'} ${selectedModel.model}`
    : selectedModel.provider

  const toggleRow = (idx) => setExpandedRows(prev => ({ ...prev, [idx]: !prev[idx] }))

  const statusClass = (s) => s === 'Compliant' ? 'compliant' : s === 'Partial' ? 'partial' : 'no-evidence'

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-neutral-200 shrink-0">
        <h2 className="text-xs font-semibold text-neutral-500 uppercase tracking-wider flex items-center gap-2">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
          Questionnaire Auto-Fill
        </h2>

        <div className="flex items-center gap-2">
          {/* Model Selector */}
          {modelsData && !isProcessing && (
            <select
              value={`${selectedModel.provider}:${selectedModel.model || ''}`}
              onChange={e => {
                const [provider, ...rest] = e.target.value.split(':')
                setSelectedModel({ provider, model: rest.join(':') || null })
              }}
              className="text-xs bg-neutral-50 border border-neutral-200 rounded px-2 py-1 text-neutral-700 outline-none focus:border-neutral-400 max-w-[200px] transition-colors"
              title="Select AI model for processing"
            >
              {modelsData.ollama?.available && modelsData.ollama.models.filter(m => !m.includes('embed')).length > 0 && (
                <optgroup label="🖥 Local (Ollama)">
                  {modelsData.ollama.models.filter(m => !m.includes('embed')).map(m => (
                    <option key={`ollama:${m}`} value={`ollama:${m}`}>{m}</option>
                  ))}
                </optgroup>
              )}
              {modelsData.groq?.available && (
                <optgroup label="⚡ Cloud (Groq — Fast)">
                  {modelsData.groq.models.map(m => (
                    <option key={`groq:${m}`} value={`groq:${m}`}>{m}</option>
                  ))}
                </optgroup>
              )}
              {modelsData.openrouter?.available && (
                <optgroup label="☁️ Cloud (OpenRouter)">
                  {modelsData.openrouter.models.map(m => (
                    <option key={`openrouter:${m}`} value={`openrouter:${m}`}>{m}</option>
                  ))}
                </optgroup>
              )}
            </select>
          )}
          {step > 1 && !isProcessing && (
            <button onClick={startOver} className="text-[11px] font-medium text-neutral-400 hover:text-neutral-600 transition-colors">Start Over</button>
          )}
        </div>
      </div>

      {/* Step Indicator */}
      <div className="questionnaire-steps border-b border-neutral-100">
        {[['Upload', 1], ['Configure', 2], ['Results', 3]].map(([label, num], i) => (
          <div key={num} className="step-item">
            {i > 0 && <div className={`step-connector ${step > num - 1 ? 'completed' : ''}`} />}
            <div className={`step-circle ${step === num ? 'active' : step > num ? 'completed' : ''}`}>
              {step > num ? '✓' : num}
            </div>
            <span className={`step-label ${step === num ? 'active' : step > num ? 'completed' : ''}`}>{label}</span>
          </div>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-5 py-4">
        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-50 border border-red-200 text-red-700 text-xs animate-fade-in">
            ⚠️ {error}
          </div>
        )}

        {/* ═══ STEP 1: Upload ═══ */}
        {step === 1 && (
          <div className="animate-fade-in">
            {!chatReady && (
              <div className="mb-4 p-3 rounded-lg bg-amber-50 border border-amber-200 text-amber-700 text-xs">
                ⚠️ Upload and process compliance documents (PDFs) first before using questionnaire auto-fill.
              </div>
            )}
            <div
              className={`questionnaire-dropzone ${isDragging ? 'dragging' : ''}`}
              onDragOver={e => { e.preventDefault(); setIsDragging(true) }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={e => { e.preventDefault(); setIsDragging(false); if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0]) }}
              onClick={() => fileInputRef.current?.click()}
            >
              <svg className="mx-auto mb-3 text-neutral-400" width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><polyline points="9 15 12 12 15 15"/></svg>
              <p className="text-sm font-medium text-neutral-600">Drop Excel (.xlsx) file here</p>
              <p className="text-xs text-neutral-400 mt-1">ISO 27001 SoA, SOC 2 checklist, HIPAA gap assessment...</p>
              <input ref={fileInputRef} type="file" accept=".xlsx" className="hidden" onChange={e => { if (e.target.files[0]) uploadFile(e.target.files[0]); e.target.value = '' }} />
            </div>

            {uploadResponse && (
              <div className="mt-4 animate-slide-up">
                <div className="flex items-center gap-2 mb-3">
                  <span className="w-2 h-2 rounded-full bg-emerald-500" />
                  <span className="text-sm font-medium text-neutral-800">{uploadResponse.filename}</span>
                  <span className="text-xs text-neutral-400">{uploadResponse.total_rows} questions</span>
                </div>
                <p className="text-[11px] text-neutral-500 mb-2">Detected columns: {uploadResponse.detected_columns.filter(c => !c.startsWith('Column_')).join(', ')}</p>

                {uploadResponse.preview_rows.length > 0 && (
                  <div className="overflow-x-auto rounded-lg border border-neutral-200">
                    <table className="preview-table">
                      <thead><tr>{uploadResponse.detected_columns.filter(c => !c.startsWith('Column_')).slice(0, 6).map(c => <th key={c}>{c}</th>)}</tr></thead>
                      <tbody>
                        {uploadResponse.preview_rows.map((row, i) => (
                          <tr key={i}>{uploadResponse.detected_columns.filter(c => !c.startsWith('Column_')).slice(0, 6).map(c => <td key={c}>{row[c] || ''}</td>)}</tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                <button onClick={() => setStep(2)} disabled={!chatReady}
                  className="mt-4 w-full py-2.5 rounded-xl bg-neutral-900 text-white text-sm font-semibold hover:bg-neutral-800 disabled:opacity-30 transition-colors">
                  Next: Configure Columns →
                </button>
              </div>
            )}
          </div>
        )}

        {/* ═══ STEP 2: Configure ═══ */}
        {step === 2 && uploadResponse && (
          <div className="animate-fade-in space-y-5">
            <div>
              <label className="block text-xs font-semibold text-neutral-700 mb-1.5">Question Column <span className="text-red-500">*</span></label>
              <select className="column-select" value={columnMapping.question_column} onChange={e => setColumnMapping(p => ({ ...p, question_column: e.target.value }))}>
                <option value="">Select column...</option>
                {uploadResponse.detected_columns.filter(c => !c.startsWith('Column_')).map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>

            {[['answer_column', 'Answer Column'], ['status_column', 'Status Column'], ['evidence_column', 'Evidence File Column'], ['page_column', 'Page Number Column'], ['confidence_column', 'Confidence Score Column']].map(([key, label]) => (
              <div key={key}>
                <label className="block text-xs font-medium text-neutral-500 mb-1.5">{label} <span className="text-neutral-300">(optional)</span></label>
                <select className="column-select" value={columnMapping[key]} onChange={e => setColumnMapping(p => ({ ...p, [key]: e.target.value }))}>
                  <option value="">— Add new column —</option>
                  {uploadResponse.detected_columns.filter(c => !c.startsWith('Column_')).map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
            ))}

            {/* Preview */}
            {columnMapping.question_column && uploadResponse.preview_rows[0] && (
              <div className="p-3 rounded-lg bg-neutral-50 border border-neutral-200">
                <p className="text-[11px] font-semibold text-neutral-500 uppercase mb-1">Sample Question</p>
                <p className="text-sm text-neutral-800">{uploadResponse.preview_rows[0][columnMapping.question_column] || '(empty)'}</p>
                <div className="mt-2 p-2 rounded bg-neutral-100 text-xs text-neutral-400 italic">Answer will appear here...</div>
              </div>
            )}

            {/* Selected model indicator */}
            <div className="flex items-center gap-2 p-2.5 rounded-lg bg-neutral-50 border border-neutral-200">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#737373" strokeWidth="2"><circle cx="12" cy="12" r="3"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/><path d="M4.93 4.93a10 10 0 0 0 0 14.14"/></svg>
              <span className="text-xs text-neutral-500">Will use: </span>
              <span className="text-xs font-semibold text-neutral-800">{modelLabel}</span>
            </div>

            <button onClick={runAutoFill} disabled={!columnMapping.question_column}
              className="w-full py-3 rounded-xl bg-neutral-900 text-white text-sm font-semibold hover:bg-neutral-800 disabled:opacity-30 transition-colors">
              Run Auto-Fill ({uploadResponse.total_rows} questions)
            </button>
          </div>
        )}

        {/* ═══ STEP 3: Processing & Results ═══ */}
        {step === 3 && (
          <div className="animate-fade-in space-y-4">
            {/* Progress */}
            {isProcessing && (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-neutral-700">
                    <span className="processing-pulse">●</span> Answering question {progress.current} of {progress.total}
                  </span>
                  <span className="text-xs text-neutral-400">{progress.total > 0 ? Math.round(progress.current / progress.total * 100) : 0}%</span>
                </div>
                <div className="questionnaire-progress-bar">
                  <div className="questionnaire-progress-fill" style={{ width: `${progress.total > 0 ? (progress.current / progress.total * 100) : 0}%` }} />
                </div>
                {progress.question && <p className="text-xs text-neutral-400 truncate">{progress.question}</p>}
              </div>
            )}

            {/* Summary */}
            {summary && (
              <div className="animate-slide-up">
                <div className="summary-grid mb-4">
                  <div className="summary-card total"><div className="value">{summary.total_processed}</div><div className="label">Total</div></div>
                  <div className="summary-card compliant"><div className="value">{summary.compliant}</div><div className="label">Compliant</div></div>
                  <div className="summary-card partial"><div className="value">{summary.partial}</div><div className="label">Partial</div></div>
                  <div className="summary-card no-evidence"><div className="value">{summary.no_evidence}</div><div className="label">No Evidence</div></div>
                </div>
                <p className="text-xs text-neutral-400 text-center mb-4">Completed in {summary.processing_time_seconds}s</p>
                {downloadToken && (
                  <div className="text-center">
                    <a href={`${API}/questionnaire/download/${downloadToken}`} className="download-btn" download>
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                      Download Filled Excel
                    </a>
                  </div>
                )}
              </div>
            )}

            {/* Live feed / Results table */}
            {results.length > 0 && (
              <div className="overflow-x-auto rounded-lg border border-neutral-200">
                <table className="results-table">
                  <thead><tr><th>#</th><th>Question</th><th>Answer</th><th>Status</th><th>Score</th></tr></thead>
                  <tbody>
                    {results.map((r, i) => (
                      <tr key={i} className={`row-${statusClass(r.status)} cursor-pointer`} onClick={() => toggleRow(i)}>
                        <td className="font-mono text-neutral-400">{r.row_index}</td>
                        <td><div className="truncate max-w-[200px]" title={r.question}>{r.question}</div></td>
                        <td>
                          <div className={expandedRows[i] ? '' : 'truncate max-w-[250px]'}>{r.answer}</div>
                          {expandedRows[i] && r.evidence_chunks?.length > 0 && (
                            <div className="mt-2 space-y-1">
                              {r.evidence_chunks.map((e, j) => (
                                <div key={j} className="text-[10px] text-neutral-400 bg-neutral-50 p-1.5 rounded">
                                  📄 {e.filename} p.{e.page_number} ({(e.relevance_score * 100).toFixed(0)}%)
                                </div>
                              ))}
                            </div>
                          )}
                        </td>
                        <td><span className={`status-badge ${statusClass(r.status)}`}>{r.status}</span></td>
                        <td className="font-mono">{(r.confidence_score * 100).toFixed(0)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
