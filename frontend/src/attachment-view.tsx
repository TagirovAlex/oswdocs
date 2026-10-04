// Окно просмотра вложения (?view=attachment&id=…&name=…&mime=…): предпросмотр
// содержимого (PDF — iframe, картинки — img) с верхней панелью Печать/Скачать/
// Закрыть, чтобы не мотать страницу за кнопками. Файл качается fetch'ем
// с Bearer-токеном (браузер не шлёт токен за субресурсами iframe/img) и
// показывается blob-адресом; адрес освобождается при закрытии окна.
import { useEffect, useRef, useState } from "react";
import { getAttachmentFile } from "./requests-client";

interface AttachmentWindowProps {
  // Числовой id вложения (валидируется: некорректный — ошибка, не запрос).
  attachmentId: string;
  // Имя файла (подпись и имя скачивания).
  fileName: string;
  // MIME из меты (может отсутствовать — тогда только скачивание).
  mime: string | null;
}

export function AttachmentWindow(props: AttachmentWindowProps) {
  const { attachmentId, fileName, mime } = props;
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string>("");
  const frameRef = useRef<HTMLIFrameElement | null>(null);

  const kind = (mime ?? "").toLowerCase();
  const isPdf = kind === "application/pdf";
  const isImage = kind.startsWith("image/");

  // Загрузка содержимого: GET /api/attachments/{id}/file с токеном сессии.
  useEffect(() => {
    let alive = true;
    let url = "";
    setBlobUrl(null);
    setLoadError("");
    const id = Number(attachmentId);
    if (!Number.isInteger(id)) {
      setLoadError("Некорректный идентификатор вложения");
      return () => {
        alive = false;
      };
    }
    getAttachmentFile(id)
      .then((blob) => {
        if (!alive) return;
        url = URL.createObjectURL(blob);
        setBlobUrl(url);
      })
      .catch((e: unknown) => {
        if (alive) setLoadError(e instanceof Error ? e.message : "Ошибка загрузки файла");
      });
    return () => {
      alive = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [attachmentId]);

  // Печать: PDF — диалог печати документа во фрейме; картинки и прочее —
  // печать окна (панель кнопок скрыта print-стилем, см. theme.css).
  function handlePrint(): void {
    if (isPdf) {
      try {
        frameRef.current?.contentWindow?.focus();
        frameRef.current?.contentWindow?.print();
        return;
      } catch {
        // Фрейм недоступен — печатаем окно целиком ниже.
      }
    }
    window.print();
  }

  // Скачивание через blob-адрес (прямая ссылка без токена дала бы 401).
  function handleDownload(): void {
    if (!blobUrl) return;
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = fileName;
    document.body?.appendChild(a);
    a.click();
    a.remove();
  }

  return (
    <div className="sed-shell">
      <main className="sed-content sed-window sed-window--wide">
        {/* Верхняя панель: все действия сразу, мотать страницу не нужно. */}
        <div className="sed-toolbar sed-previewbar" aria-label="Действия с вложением">
          <button type="button" className="sed-btn" onClick={handlePrint} disabled={!blobUrl}>
            Печать
          </button>
          <button
            type="button"
            className="sed-btn sed-btn--ghost"
            onClick={handleDownload}
            disabled={!blobUrl}
          >
            Скачать
          </button>
          <button
            type="button"
            className="sed-btn sed-btn--ghost"
            onClick={() => window.close()}
          >
            Закрыть
          </button>
          <span className="sed-note">{fileName}</span>
        </div>
        {loadError && <div role="alert">{loadError}</div>}
        {!blobUrl && !loadError && <div className="sed-note">Загрузка файла…</div>}
        {blobUrl && isPdf && (
          <iframe
            ref={frameRef}
            title={fileName}
            src={blobUrl}
            style={{ width: "100%", height: "70vh", border: "none" }}
          />
        )}
        {blobUrl && isImage && (
          <img src={blobUrl} alt={fileName} style={{ maxWidth: "100%" }} />
        )}
        {blobUrl && !isPdf && !isImage && (
          <div className="sed-note">Предпросмотр недоступен — скачайте файл.</div>
        )}
      </main>
    </div>
  );
}
