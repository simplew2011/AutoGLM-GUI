import React, { useState, useCallback, useRef, useEffect } from 'react';
import {
  Bot,
  Loader2,
  AlertCircle,
  ImagePlus,
  X,
  CheckCircle2,
  Send,
} from 'lucide-react';
import {
  detectIntent,
  createTaskSession,
  submitTaskSessionTask,
  getErrorMessage,
  type TaskImageAttachment,
} from '../api';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { useTranslation } from '../lib/i18n-context';
import { ImagePreview } from '@/components/ui/image-preview';
import { DevicePanel } from './DevicePanel';
import { ChatKitPanel } from './ChatKitPanel';
import { ChatAgentPanel } from './ChatAgentPanel';

type Phase = 'input' | 'detecting' | 'result' | 'executing';

interface AutoModePanelProps {
  deviceId: string;
  deviceSerial: string;
  deviceName?: string;
  deviceConnectionType?: string;
  isConfigured?: boolean;
  isVisible?: boolean;
  unlimitedStepsEnabled?: boolean;
  resetTrigger?: number;
  onExecutingChange?: (executing: boolean) => void;
}

const MAX_IMAGE_ATTACHMENTS = 3;

const COUNTDOWN_SECONDS = 5;

function getSessionStorageKey(mode: string, deviceSerial: string): string {
  switch (mode) {
    case 'classic':
      return `autoglm:classic-session:${deviceSerial}`;
    case 'layered':
      return `layered-task-session:${deviceSerial}`;
    case 'chat':
      return 'autoglm:chat-session';
    default:
      return `autoglm:auto-session:${deviceSerial}`;
  }
}

export function AutoModePanel({
  deviceId,
  deviceSerial,
  deviceName = '',
  deviceConnectionType,
  isConfigured = false,
  isVisible = true,
  unlimitedStepsEnabled = false,
  resetTrigger,
  onExecutingChange,
}: AutoModePanelProps) {
  const t = useTranslation();
  const [phase, setPhase] = useState<Phase>('input');
  const [inputValue, setInputValue] = useState('');
  const [detectedMode, setDetectedMode] = useState<
    'classic' | 'layered' | 'chat'
  >('classic');
  const [selectedMode, setSelectedMode] = useState<
    'classic' | 'layered' | 'chat'
  >('classic');
  const [apiError, setApiError] = useState<string | null>(null);
  const currentMessageRef = useRef('');

  const [attachments, setAttachments] = useState<TaskImageAttachment[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Track session info for reset-trigger cancellation
  const sessionRef = useRef<string | null>(null);
  const taskRef = useRef<string | null>(null);
  const cancelTaskRunRef = useRef<((id: string) => Promise<unknown>) | null>(
    null
  );

  // Import cancelTaskRun lazily to avoid circular import issues
  useEffect(() => {
    import('../api').then(m => {
      cancelTaskRunRef.current = m.cancelTaskRun;
    });
  }, []);

  // Handle reset trigger: cancel current task and go back to input
  useEffect(() => {
    if (resetTrigger === undefined || resetTrigger === 0) return;

    const doReset = async () => {
      if (taskRef.current && cancelTaskRunRef.current) {
        try {
          await cancelTaskRunRef.current(taskRef.current);
        } catch {
          // ignore
        }
      }
      taskRef.current = null;
      sessionRef.current = null;
      setPhase('input');
      setApiError(null);
      setInputValue('');
      setAttachments([]);
      onExecutingChange?.(false);
    };
    void doReset();
  }, [resetTrigger, onExecutingChange]);

  // Cancel running task on unmount (e.g. user switches to another tab)
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  useEffect(() => {
    return () => {
      if (
        phaseRef.current === 'executing' &&
        taskRef.current &&
        cancelTaskRunRef.current
      ) {
        cancelTaskRunRef.current(taskRef.current).catch(() => {});
      }
    };
  }, []);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    for (const file of Array.from(files)) {
      if (attachments.length >= MAX_IMAGE_ATTACHMENTS) break;
      const reader = new FileReader();
      reader.onload = () => {
        const result = reader.result as string;
        const commaIdx = result.indexOf(',');
        const mime = result.slice(5, result.indexOf(';'));
        setAttachments(prev => [
          ...prev,
          { mime_type: mime, data: result.slice(commaIdx + 1) },
        ]);
      };
      reader.readAsDataURL(file);
    }
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const removeAttachment = (idx: number) => {
    setAttachments(prev => prev.filter((_, i) => i !== idx));
  };

  const handlePaste = (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of Array.from(items)) {
      if (attachments.length >= MAX_IMAGE_ATTACHMENTS) break;
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile();
        if (!file) continue;
        const reader = new FileReader();
        reader.onload = () => {
          const result = reader.result as string;
          const commaIdx = result.indexOf(',');
          setAttachments(prev => [
            ...prev,
            { mime_type: item.type, data: result.slice(commaIdx + 1) },
          ]);
        };
        reader.readAsDataURL(file);
      }
    }
  };

  const startExecution = useCallback(
    async (mode: 'classic' | 'layered' | 'chat') => {
      const message = currentMessageRef.current;
      setApiError(null);

      try {
        // Create session with the right mode (use chat device for chat mode)
        const sessionDeviceId = mode === 'chat' ? '__chat__' : deviceId;
        const sessionSerial = mode === 'chat' ? 'chat' : deviceSerial;
        const session = await createTaskSession(
          sessionDeviceId,
          sessionSerial,
          mode
        );

        // Store session ID so the panel can pick it up
        const key = getSessionStorageKey(mode, sessionSerial);
        sessionStorage.setItem(key, session.id);

        // Submit the user's message as the first task
        const task = await submitTaskSessionTask(
          session.id,
          message,
          attachments.length > 0 ? attachments : undefined
        );

        sessionRef.current = session.id;
        taskRef.current = task.id;

        // Only switch to execution phase AFTER session is ready
        setPhase('executing');
        onExecutingChange?.(true);
      } catch (err: unknown) {
        const msg = getErrorMessage(err);
        setApiError(msg);
        setPhase('input');
        onExecutingChange?.(false);
      }
    },
    [deviceId, deviceSerial, attachments, onExecutingChange]
  );

  const handleSubmit = useCallback(async () => {
    if (!inputValue.trim()) return;
    const message = inputValue.trim();
    currentMessageRef.current = message;
    setInputValue('');
    setApiError(null);
    setPhase('detecting');

    try {
      const result = await detectIntent(message);
      const mode = result.mode as 'classic' | 'layered' | 'chat';
      setDetectedMode(mode);
      setSelectedMode(mode);
      setPhase('result');
    } catch (err: unknown) {
      const msg = getErrorMessage(err);
      if (
        msg.includes('not configured') ||
        msg.includes('503') ||
        msg.includes('Service Unavailable')
      ) {
        setApiError(
          t.chatkit?.intentNotConfigured ||
            'Intent detection model not configured'
        );
      } else {
        setApiError(msg);
      }
      setPhase('input');
    }
  }, [inputValue, t]);

  const handleConfirm = useCallback(() => {
    startExecution(selectedMode || detectedMode);
  }, [selectedMode, detectedMode, startExecution]);

  // Countdown timer
  const [countdown, setCountdown] = useState(COUNTDOWN_SECONDS);

  useEffect(() => {
    if (phase !== 'result') return;

    let seconds = COUNTDOWN_SECONDS;
    setCountdown(seconds);

    const timer = setInterval(() => {
      seconds -= 1;
      setCountdown(seconds);
      if (seconds <= 0) {
        clearInterval(timer);
      }
    }, 1000);

    return () => clearInterval(timer);
  }, [phase, detectedMode]);

  // Auto-submit when countdown reaches 0
  useEffect(() => {
    if (phase === 'result' && countdown === 0) {
      startExecution(selectedMode || detectedMode);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [countdown]);

  // Execution phase: delegate to the appropriate panel
  if (phase === 'executing') {
    const resolvedMode = selectedMode || detectedMode;

    if (resolvedMode === 'chat') {
      return (
        <div className="w-full max-w-4xl flex items-stretch justify-center min-h-0">
          <ChatAgentPanel />
        </div>
      );
    }

    if (resolvedMode === 'layered') {
      return (
        <div className="w-full flex items-stretch justify-center">
          <ChatKitPanel
            deviceId={deviceId}
            deviceSerial={deviceSerial}
            deviceName={deviceName}
            deviceConnectionType={deviceConnectionType}
            isVisible={isVisible}
            unlimitedStepsEnabled={unlimitedStepsEnabled}
          />
        </div>
      );
    }

    // classic mode
    return (
      <div className="w-full flex items-stretch justify-center">
        <DevicePanel
          deviceId={deviceId}
          deviceSerial={deviceSerial}
          deviceName={deviceName}
          deviceConnectionType={deviceConnectionType}
          isConfigured={isConfigured}
          isVisible={isVisible}
          unlimitedStepsEnabled={unlimitedStepsEnabled}
        />
      </div>
    );
  }

  // Input / Detecting / Result phases
  return (
    <div className="flex-1 flex flex-col items-center justify-center min-h-0 p-4">
      <div className="w-full max-w-2xl space-y-4">
        {phase === 'input' && (
          <>
            <div className="flex flex-col items-center justify-center text-center py-8">
              <div className="flex h-20 w-20 items-center justify-center rounded-full bg-[#1d9bf0]/10 mb-6">
                <Bot className="h-10 w-10 text-[#1d9bf0]" />
              </div>
              <h2 className="text-xl font-semibold text-slate-900 dark:text-slate-100">
                {t.chatkit?.autoMode || '自动模式'}
              </h2>
              <p className="mt-2 text-sm text-slate-500 dark:text-slate-400 max-w-md">
                {t.chatkit?.autoModeDesc ||
                  '智能识别意图，自动选择最佳执行模式'}
              </p>
            </div>

            {apiError && (
              <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-950/30 text-red-700 dark:text-red-400 text-sm">
                <AlertCircle className="w-4 h-4 flex-shrink-0" />
                {apiError}
              </div>
            )}

            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              multiple
              className="hidden"
              onChange={handleFileSelect}
            />

            {attachments.length > 0 && (
              <div className="mb-3 flex flex-wrap gap-2">
                {attachments.map((att, idx) => (
                  <div key={idx} className="relative">
                    <ImagePreview
                      src={`data:${att.mime_type};base64,${att.data}`}
                      alt={`Attachment ${idx + 1}`}
                      className="h-16 w-16 object-cover rounded-lg"
                    />
                    <button
                      onClick={() => removeAttachment(idx)}
                      className="absolute -right-1 -top-1 flex h-5 w-5 items-center justify-center rounded-full bg-slate-600 text-white hover:bg-slate-700"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
            )}

            <div className="flex items-end gap-3">
              <Textarea
                value={inputValue}
                onChange={e => setInputValue(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSubmit();
                  }
                }}
                onPaste={handlePaste}
                placeholder={
                  t.devicePanel?.whatToDo || 'Describe what you want to do...'
                }
                className="flex-1 min-h-[40px] max-h-[120px] resize-none"
                disabled={phase !== 'input'}
                rows={1}
              />

              <Button
                type="button"
                variant="outline"
                size="icon"
                disabled={attachments.length >= MAX_IMAGE_ATTACHMENTS}
                className="h-10 w-10 flex-shrink-0"
                onClick={() => fileInputRef.current?.click()}
              >
                <ImagePlus className="w-4 h-4" />
              </Button>

              <Button
                size="icon"
                onClick={handleSubmit}
                disabled={!inputValue.trim() || phase !== 'input'}
                className="h-10 w-10 rounded-full flex-shrink-0 bg-[#1d9bf0] text-white hover:bg-[#1a8cd8]"
              >
                <Send className="w-4 h-4" />
              </Button>
            </div>
          </>
        )}

        {phase === 'detecting' && (
          <div className="flex flex-col items-center justify-center py-16 space-y-4">
            <div className="flex h-16 w-16 items-center justify-center rounded-full bg-[#1d9bf0]/10">
              <Loader2 className="h-8 w-8 text-[#1d9bf0] animate-spin" />
            </div>
            <p className="text-slate-500 dark:text-slate-400 text-sm">
              {t.chatkit?.detecting || '正在识别意图...'}
            </p>
          </div>
        )}

        {phase === 'result' && (
          <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-6 space-y-5 shadow-lg">
            <div>
              <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
                {t.chatkit?.intentDetectTitle || '用户意图自动识别'}
              </p>
            </div>

            <div className="space-y-2">
              {(['classic', 'layered', 'chat'] as const).map(mode => (
                <label
                  key={mode}
                  className={`flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${
                    selectedMode === mode
                      ? 'border-[#1d9bf0] bg-[#1d9bf0]/5'
                      : 'border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600'
                  }`}
                >
                  <input
                    type="radio"
                    name="intent-mode"
                    value={mode}
                    checked={selectedMode === mode}
                    onChange={() => setSelectedMode(mode)}
                    className="w-4 h-4 text-[#1d9bf0] focus:ring-[#1d9bf0]"
                  />
                  <div className="flex-1">
                    <span className="text-sm font-medium text-slate-900 dark:text-slate-100">
                      {mode === 'classic'
                        ? t.chatkit?.classicMode || '经典模式'
                        : mode === 'layered'
                          ? t.chatkit?.layeredMode || '分层代理'
                          : t.chatkit?.chatMode || '对话模式'}
                    </span>
                    <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                      {mode === 'classic'
                        ? t.chatkit?.classicModeDesc || '视觉模型直接执行任务'
                        : mode === 'layered'
                          ? t.chatkit?.layeredModeDesc ||
                            '规划层分解任务，执行层独立完成子任务'
                          : t.chatkit?.chatModeDesc ||
                            '纯文本/图片对话，不操作设备'}
                    </p>
                  </div>
                </label>
              ))}
            </div>

            <div className="flex items-center gap-3">
              <Button onClick={handleConfirm} variant="twitter">
                <CheckCircle2 className="w-4 h-4 mr-2" />
                {t.chatkit?.intentConfirm || '确认'}（{countdown}s）
              </Button>
              <span className="text-xs text-slate-400">
                {countdown > 0
                  ? (
                      t.chatkit?.intentAutoCountdown ||
                      '{countdown} 秒后自动确认'
                    ).replace('{countdown}', String(countdown))
                  : '正在确认...'}
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
