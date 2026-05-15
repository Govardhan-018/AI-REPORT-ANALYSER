import { useState, useRef, useCallback } from 'react'

export default function DocumentPanel({ documents, onDocumentsChange, apiBase }) {
  const [isDragging, setIsDragging] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(null) // { filename, progress, message }
  const fileInputRef = useRef(null)
  const pollingRef = useRef({})

  const handleDragOver = (e) => {
    e.preventDefault()
    setIsDragging(true)
  }
  const handleDragLeave = () => setIsDragging(false)

  const handleDrop = (e) => {
    e.preventDefault()
    setIsDragging(false)
    if (e.dataTransfer.files.length > 0) uploadFile(e.dataTransfer.files[0])
  }

  const handleFileSelect = (e) => {
    if (e.target.files.length > 0) {
      uploadFile(e.target.files[0])
      e.target.value = ''
    }
  }

  const uploadFile = async (file) => {
    const ext = file.name.split('.').pop().toLowerCase()
    if (!['pdf'].includes(ext)) return
    if (file.size > 100 * 1024 * 1024) return

    setUploadProgress({ filename: file.name, progress: 5, message: 'Uploading...' })

    const formData = new FormData()
    formData.append('file', file)

    try {
      const res = await fetch(`${apiBase}/api/upload`, { method: 'POST', body: formData })
      if (!res.ok) throw new Error('Upload failed')
      const data = await res.json()
      startPolling(data.filename)
    } catch {
      setUploadProgress(null)
    }
  }

  const startPolling = (filename) => {
    const poll = async () => {
      try {
        const res = await fetch(`${apiBase}/api/upload/status/${encodeURIComponent(filename)}`)
        if (!res.ok) return
        const data = await res.json()
        setUploadProgress({ filename, progress: data.progress || 0, message: data.message || 'Processing...' })

        if (data.status === 'ready' || data.status === 'error') {
          clearInterval(pollingRef.current[filename])
          delete pollingRef.current[filename]
          setUploadProgress(null)
          onDocumentsChange()
        }
      } catch { /* ignore */ }
    }
    poll()
    pollingRef.current[filename] = setInterval(poll, 2000)
  }

  const deleteDocument = async (filename) => {
    try {
      const res = await fetch(`${apiBase}/api/documents/${encodeURIComponent(filename)}`, { method: 'DELETE' })
      if (res.ok) onDocumentsChange()
    } catch { /* ignore */ }
  }

  const formatSize = (bytes) => {
    if (!bytes) return ''
    if (bytes < 1024) return bytes + ' B'
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
  }

  const readyCount = documents.filter(d => d.status === 'ready' || (d.chunk_count > 0 && d.status !== 'error')).length

  return (
    <div className="flex flex-col h-full">
      {/* Upload Zone */}
      <div className="p-4 border-b border-neutral-100">
        <h2 className="text-xs font-semibold text-neutral-500 uppercase tracking-wider mb-3 flex items-center gap-2">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
          Upload
        </h2>

        <div
          className={`relative border-2 border-dashed rounded-lg p-6 text-center cursor-pointer transition-all duration-200 ${
            isDragging ? 'border-neutral-900 bg-neutral-50' : 'border-neutral-300 hover:border-neutral-400 hover:bg-neutral-50'
          }`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => !uploadProgress && fileInputRef.current?.click()}
        >
          {uploadProgress ? (
            <div className="animate-fade-in">
              <p className="text-xs font-medium text-neutral-700 mb-2 truncate">{uploadProgress.filename}</p>
              <div className="w-full h-1.5 bg-neutral-200 rounded-full overflow-hidden mb-2">
                <div
                  className="h-full bg-neutral-800 rounded-full transition-all duration-500"
                  style={{ width: `${uploadProgress.progress}%` }}
                />
              </div>
              <p className="text-[11px] text-neutral-500">{uploadProgress.message}</p>
            </div>
          ) : (
            <>
              <svg className="mx-auto mb-2 text-neutral-400" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><polyline points="9 15 12 12 15 15"/>
              </svg>
              <p className="text-xs text-neutral-600 font-medium">Drop PDF here</p>
              <p className="text-[11px] text-neutral-400 mt-0.5">or click to browse</p>
            </>
          )}
          <input ref={fileInputRef} type="file" accept=".pdf" className="hidden" onChange={handleFileSelect} />
        </div>
      </div>

      {/* Documents List */}
      <div className="flex-1 overflow-y-auto">
        <div className="px-4 py-3 flex items-center justify-between">
          <h2 className="text-xs font-semibold text-neutral-500 uppercase tracking-wider flex items-center gap-2">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
            Documents
          </h2>
          <span className="text-[11px] font-medium text-neutral-400 bg-neutral-100 px-2 py-0.5 rounded-full">
            {readyCount}
          </span>
        </div>

        {documents.length === 0 ? (
          <div className="px-4 py-8 text-center">
            <svg className="mx-auto mb-2 text-neutral-300" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>
            </svg>
            <p className="text-xs text-neutral-400">No documents uploaded</p>
          </div>
        ) : (
          <div className="px-3 pb-3 space-y-1">
            {documents.map((doc) => {
              const isReady = doc.status === 'ready' || (doc.chunk_count > 0 && doc.status !== 'error')
              const isProcessing = doc.status === 'processing'
              const isError = doc.status === 'error'

              return (
                <div
                  key={doc.filename}
                  className="group flex items-start gap-2.5 p-2.5 rounded-lg hover:bg-neutral-50 transition-colors animate-fade-in"
                >
                  <div className="shrink-0 mt-0.5">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={isError ? '#dc2626' : '#737373'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>
                    </svg>
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-[13px] font-medium text-neutral-800 truncate" title={doc.filename}>{doc.filename}</p>
                    <div className="flex items-center gap-1.5 mt-0.5">
                      {isProcessing && <span className="spinner" />}
                      <p className="text-[11px] text-neutral-400">
                        {isReady && `${doc.page_count || '?'} pages · ${doc.chunk_count} chunks`}
                        {isProcessing && 'Processing...'}
                        {isError && 'Error'}
                        {doc.file_size ? ` · ${formatSize(doc.file_size)}` : ''}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <span className={`inline-block w-1.5 h-1.5 rounded-full ${isReady ? 'bg-emerald-500' : isProcessing ? 'bg-amber-400' : 'bg-red-500'}`} />
                    <button
                      onClick={(e) => { e.stopPropagation(); deleteDocument(doc.filename) }}
                      className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-neutral-200 transition-all text-neutral-400 hover:text-neutral-600"
                      title="Delete"
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                      </svg>
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
