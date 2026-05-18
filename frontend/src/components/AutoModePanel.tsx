import React, { useState, useCallback, useRef, useEffect } from 'react';
import {
  Send,
  Bot,
  Sparkles,
  Layers,
  MessageSquare,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Square,
  RotateCcw,
} from 'lucide-react';
import {
  detectIntent,
  createTaskSession,
  submitTaskSessionTask,
  streamTaskEvents,
  cancelTaskRun,
  getErrorMessage,
  type TaskEventRecordResponse,
} from '../api';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { useTranslation } from '../lib/i18n-context';
import { DeviceMonitor } from './DeviceMonitor';

type Phase = 'input' | 'detecting' | 'result' | 'executing';

interface AutoModePanelProps {
  deviceId: string;
  deviceSerial: string;
}

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export function AutoModePanel({ deviceId, deviceSerial }: AutoModePanelProps) {
  const t = useTranslation();
  const [phase, setPhase] = useState<Phase>('input');
  const [inputValue, setInputValue] = useState('');
  const [detectedMode, setDetectedMode] = useState<
    'classic' | 'layered' | 'chat'
  >('classic');
  const [apiError, setApiError] = useState<string | null>(null);
  const currentMessageRef = useRef('');

  const [currentTaskId, setCurrentTaskId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isAborting, setIsAborting] = useState(false);
  const streamCloserRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    return () => {
      if (streamCloserRef.current) {
        streamCloserRef.current();
      }
    };
  }, []);

  const startExecution = useCallback(
    async (mode: 'classic' | 'layered' | 'chat') => {
      const message = currentMessageRef.current;
      setPhase('executing');
      setIsStreaming(true);
      setApiError(null);

      try {
        const session = await createTaskSession(deviceId, deviceSerial, mode);

        const task = await submitTaskSessionTask(session.id, message);
        setCurrentTaskId(task.id);

        setMessages([{ role: 'user', content: message }]);

        let lastContent = '';
        const closer = streamTaskEvents(
          task.id,
          (event: TaskEventRecordResponse) => {
            if (event.event_type === 'done') {
              const payload = event.payload as {
                message?: string;
                success?: boolean;
              };
              if (payload?.message) {
                setMessages(prev => {
                  if (
                    prev.length > 0 &&
                    prev[prev.length - 1].role === 'assistant'
                  ) {
                    const updated = [...prev];
                    updated[updated.length - 1] = {
                      role: 'assistant',
                      content: payload.message || '',
                    };
                    return updated;
                  }
                  return [
                    ...prev,
                    { role: 'assistant', content: payload.message || '' },
                  ];
                });
              }
              setIsStreaming(false);
            } else if (event.event_type === 'thinking') {
              const payload = event.payload as { chunk?: string };
              if (payload?.chunk) {
                lastContent += payload.chunk;
                setMessages(prev => {
                  if (
                    prev.length > 0 &&
                    prev[prev.length - 1].role === 'assistant'
                  ) {
                    const updated = [...prev];
                    updated[updated.length - 1] = {
                      role: 'assistant',
                      content: lastContent,
                    };
                    return updated;
                  }
                  return [...prev, { role: 'assistant', content: lastContent }];
                });
              }
            } else if (
              event.event_type === 'error' ||
              event.event_type === 'cancelled'
            ) {
              setIsStreaming(false);
            } else if (event.event_type === 'step') {
              const payload = event.payload as { message?: string };
              lastContent = payload?.message || lastContent;
              setMessages(prev => {
                if (
                  prev.length > 0 &&
                  prev[prev.length - 1].role === 'assistant'
                ) {
                  const updated = [...prev];
                  updated[updated.length - 1] = {
                    role: 'assistant',
                    content: lastContent,
                  };
                  return updated;
                }
                return [...prev, { role: 'assistant', content: lastContent }];
              });
            }
          },
          (errorMsg: string) => {
            setApiError(errorMsg);
            setIsStreaming(false);
          }
        );
        streamCloserRef.current = closer.close;
      } catch (err: unknown) {
        const msg = getErrorMessage(err);
        setApiError(msg);
        setIsStreaming(false);
        setPhase('input');
      }
    },
    [deviceId, deviceSerial]
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
      setDetectedMode(result.mode as 'classic' | 'layered' | 'chat');
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
    startExecution(detectedMode);
  }, [detectedMode, startExecution]);

  const handleSwitchMode = useCallback(
    (mode: 'classic' | 'layered' | 'chat') => {
      startExecution(mode);
    },
    [startExecution]
  );

  const handleReset = useCallback(async () => {
    if (streamCloserRef.current) {
      streamCloserRef.current();
      streamCloserRef.current = null;
    }
    if (currentTaskId && isStreaming) {
      try {
        await cancelTaskRun(currentTaskId);
      } catch {
        // ignore
      }
    }
    setPhase('input');
    setCurrentTaskId(null);
    setMessages([]);
    setIsStreaming(false);
    setIsAborting(false);
    setApiError(null);
    setInputValue('');
  }, [currentTaskId, isStreaming]);

  const handleAbort = useCallback(async () => {
    setIsAborting(true);
    if (streamCloserRef.current) {
      streamCloserRef.current();
      streamCloserRef.current = null;
    }
    if (currentTaskId) {
      try {
        await cancelTaskRun(currentTaskId);
      } catch {
        // ignore
      }
    }
    setIsStreaming(false);
    setIsAborting(false);
  }, [currentTaskId]);

  if (phase === 'executing') {
    return (
      <div className="w-full max-w-7xl flex items-stretch gap-0 justify-center min-h-0 overflow-hidden">
        <DeviceMonitor deviceId={deviceId} className="rounded-l-xl" />
        <div className="flex-1 flex flex-col min-h-0 bg-white dark:bg-slate-900 rounded-r-xl border border-l-0 border-slate-200 dark:border-slate-700">
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {messages.map((msg, idx) => (
              <div
                key={idx}
                className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className={`max-w-[80%] rounded-lg p-3 ${
                    msg.role === 'user'
                      ? 'bg-[#1d9bf0] text-white'
                      : 'bg-slate-100 dark:bg-slate-800 text-slate-900 dark:text-slate-100'
                  }`}
                >
                  <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
                </div>
              </div>
            ))}
            {isStreaming &&
              messages.length > 0 &&
              messages[messages.length - 1].role === 'assistant' && (
                <div className="flex justify-start">
                  <div className="bg-slate-100 dark:bg-slate-800 rounded-lg p-3">
                    <Loader2 className="w-4 h-4 animate-spin text-slate-400" />
                  </div>
                </div>
              )}
            {apiError && (
              <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-950/30 text-red-700 dark:text-red-400 text-sm">
                <AlertCircle className="w-4 h-4 flex-shrink-0" />
                {apiError}
              </div>
            )}
          </div>
          <div className="p-4 border-t border-slate-200 dark:border-slate-700 flex gap-2">
            <Button
              variant="outline"
              onClick={handleReset}
              disabled={isStreaming || isAborting}
            >
              <RotateCcw className="w-4 h-4 mr-2" />
              Reset
            </Button>
            {isStreaming && (
              <Button
                variant="outline"
                onClick={handleAbort}
                disabled={isAborting}
              >
                <Square className="w-4 h-4 mr-2" />
                {isAborting ? 'Aborting...' : 'Abort'}
              </Button>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col items-center justify-center min-h-0 p-4">
      <div className="w-full max-w-2xl space-y-4">
        {phase === 'input' && (
          <>
            <div className="text-center space-y-2">
              <Bot className="w-12 h-12 text-[#1d9bf0] mx-auto" />
              <h2 className="text-xl font-semibold text-slate-900 dark:text-slate-100">
                {t.chatkit?.autoMode || '自动模式'}
              </h2>
              <p className="text-sm text-slate-500 dark:text-slate-400">
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

            <div className="relative">
              <Textarea
                value={inputValue}
                onChange={e => setInputValue(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSubmit();
                  }
                }}
                placeholder="Describe what you want to do..."
                className="pr-12 min-h-[80px] resize-none"
                disabled={phase !== 'input'}
              />
              <Button
                size="icon"
                onClick={handleSubmit}
                disabled={!inputValue.trim() || phase !== 'input'}
                className="absolute right-2 bottom-2"
                variant="twitter"
              >
                <Send className="w-4 h-4" />
              </Button>
            </div>
          </>
        )}

        {phase === 'detecting' && (
          <div className="text-center space-y-4 py-12">
            <Loader2 className="w-8 h-8 text-[#1d9bf0] mx-auto animate-spin" />
            <p className="text-slate-500 dark:text-slate-400">
              {t.chatkit?.detecting || '正在识别意图...'}
            </p>
          </div>
        )}

        {phase === 'result' && (
          <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-6 space-y-4 shadow-lg">
            <div className="flex items-center gap-3">
              <Bot className="w-8 h-8 text-[#1d9bf0]" />
              <div>
                <p className="text-sm text-slate-500 dark:text-slate-400">
                  {t.chatkit?.intentDetected || '意图识别结果'}
                </p>
                <p className="text-lg font-semibold text-slate-900 dark:text-slate-100">
                  {detectedMode === 'classic'
                    ? t.chatkit?.classicMode || '经典模式'
                    : detectedMode === 'layered'
                      ? t.chatkit?.layeredMode || '分层代理'
                      : t.chatkit?.chatMode || '对话模式'}
                </p>
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button onClick={handleConfirm} variant="twitter">
                <CheckCircle2 className="w-4 h-4 mr-2" />
                {t.chatkit?.confirmExecute || '确认执行'}
              </Button>
              {detectedMode !== 'classic' && (
                <Button
                  variant="outline"
                  onClick={() => handleSwitchMode('classic')}
                >
                  <Sparkles className="w-4 h-4 mr-2" />
                  {t.chatkit?.switchToClassic || '切换为经典模式'}
                </Button>
              )}
              {detectedMode !== 'layered' && (
                <Button
                  variant="outline"
                  onClick={() => handleSwitchMode('layered')}
                >
                  <Layers className="w-4 h-4 mr-2" />
                  {t.chatkit?.switchToLayered || '切换为分层代理'}
                </Button>
              )}
              {detectedMode !== 'chat' && (
                <Button
                  variant="outline"
                  onClick={() => handleSwitchMode('chat')}
                >
                  <MessageSquare className="w-4 h-4 mr-2" />
                  {t.chatkit?.switchToChat || '切换为对话模式'}
                </Button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
