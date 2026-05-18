import ReactMarkdown from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';

interface MarkdownContentProps {
  content: string;
  className?: string;
  prose?: boolean;
}

export function MarkdownContent({
  content,
  className = '',
  prose = true,
}: MarkdownContentProps) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkBreaks]}
      className={`${prose ? 'prose dark:prose-invert max-w-none prose-pre:text-sm prose-code:text-sm' : 'whitespace-pre-wrap'} ${className}`}
      components={
        {
          table: ({ ...props }) => (
            <div className="overflow-x-auto">
              <table {...props} />
            </div>
          ),
          a: ({ href, children, ...props }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 dark:text-blue-400 underline underline-offset-2 hover:text-blue-800 dark:hover:text-blue-300"
              {...props}
            >
              {children}
            </a>
          ),
          code: ({ ...props }) => {
            const isInline = !props.className?.includes('language-');
            return isInline ? (
              <code className="whitespace-nowrap" {...props} />
            ) : (
              <code className="whitespace-pre-wrap" {...props} />
            );
          },
        } as Components
      }
    >
      {content}
    </ReactMarkdown>
  );
}
