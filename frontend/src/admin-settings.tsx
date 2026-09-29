// Админка настроек для SED_ADMINS (волна B4).
// Значения — из settings БД (GET/PUT /api/settings), в коде не хардкодятся.
// Все персональные данные отсутствуют (только технические настройки).
import { useEffect, useState } from "react";
import { getSettings, saveSettings } from "./settings-client";
import type { SettingsData } from "./settings-client";
import type { Role } from "./api-mock";

interface AdminSettingsProps {
  // Роль (форма — только SED_ADMINS; проверку доступа делает сервер, 403 для не-админа).
  role: Role;
}

// Админка: TTL отметок + лимиты скана + флаги процесса.
export function AdminSettings(props: AdminSettingsProps) {
  void props; // Доступ проверяет сервер (403), на клиенте роль не нужна.
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string>("");
  const [saveError, setSaveError] = useState<string>("");
  const [saved, setSaved] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  // Значения формы — только из API (без хардкод-дефолтов).
  const [ttl, setTtl] = useState<number | null>(null);
  const [retentionDays, setRetentionDays] = useState<number | null>(null);
  const [maxMb, setMaxMb] = useState<number | null>(null);
  const [paperRequired, setPaperRequired] = useState<boolean | null>(null);
  const [smtpFrom, setSmtpFrom] = useState<string | null>(null);

  // Загрузка настроек с сервера (доступ — только админам, иначе 403).
  useEffect(() => {
    let alive = true;
    getSettings()
      .then((data) => {
        if (alive) {
          setTtl(data.approval_ttl_days);
          setRetentionDays(data.scan_retention_days);
          setMaxMb(data.scan_max_mb);
          setPaperRequired(data.require_paper_signature);
          setSmtpFrom(data.smtp_from);
          setError("");
        }
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : "Ошибка загрузки настроек");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  // Сохранение: PUT /api/settings; ошибки 403/422/503 приходят понятным текстом из клиента.
  async function handleSave(): Promise<void> {
    if (busy) return;
    setSaveError("");
    setSaved("");
    // Пустые поля (ключа нет в БД) — честно просим заполнить, а не подставляем дефолты.
    if (
      ttl === null ||
      retentionDays === null ||
      maxMb === null ||
      paperRequired === null ||
      smtpFrom === null
    ) {
      setSaveError("Заполните все поля настроек (значения хранятся в settings БД)");
      return;
    }
    setBusy(true);
    try {
      const data: SettingsData = {
        approval_ttl_days: ttl,
        scan_retention_days: retentionDays,
        scan_max_mb: maxMb,
        require_paper_signature: paperRequired,
        smtp_from: smtpFrom,
      };
      const result = await saveSettings(data);
      setSaved(
        `Сохранено: TTL=${result.approval_ttl_days} дн., сканы ${result.scan_retention_days} дн./${result.scan_max_mb} МБ, от ${result.smtp_from}`,
      );
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : "Ошибка сохранения настроек");
    } finally {
      setBusy(false);
    }
  }

  // Ошибка загрузки (в т.ч. 403 для не-админа) — alert вместо формы.
  if (error) return <div role="alert">Ошибка: {error}</div>;
  if (loading) return <div className="sed-note">Загрузка настроек…</div>;

  return (
    <section aria-label="Настройки СЭД">
      <h3>Настройки (только SED_ADMINS)</h3>
      <div className="sed-note">Значения хранятся в settings БД и применяются без пересборки.</div>
      <label style={{ display: "block", marginTop: 8 }}>
        TTL отметок, дней (approval_ttl_days)
        <input
          aria-label="TTL отметок"
          type="number"
          value={ttl ?? ""}
          onChange={(e) => setTtl(Number(e.target.value))}
        />
      </label>
      <label style={{ display: "block", marginTop: 8 }}>
        Хранение сканов, дней (scan_retention_days)
        <input
          aria-label="Хранение сканов"
          type="number"
          value={retentionDays ?? ""}
          onChange={(e) => setRetentionDays(Number(e.target.value))}
        />
      </label>
      <label style={{ display: "block", marginTop: 8 }}>
        Лимит скана, МБ (scan_max_mb)
        <input
          aria-label="Лимит скана"
          type="number"
          value={maxMb ?? ""}
          onChange={(e) => setMaxMb(Number(e.target.value))}
        />
      </label>
      <label style={{ display: "block", marginTop: 8 }}>
        <input
          type="checkbox"
          checked={paperRequired ?? false}
          onChange={(e) => setPaperRequired(e.target.checked)}
        />
        Требовать бумажное заявление (require_paper_signature)
      </label>
      <label style={{ display: "block", marginTop: 8 }}>
        Отправитель уведомлений, e-mail (smtp_from)
        <input
          aria-label="Отправитель уведомлений"
          type="email"
          value={smtpFrom ?? ""}
          onChange={(e) => setSmtpFrom(e.target.value)}
        />
      </label>
      <div className="sed-toolbar" style={{ marginTop: 12 }}>
        <button type="button" className="sed-btn" onClick={handleSave} disabled={busy}>
          {busy ? "Сохранение…" : "Сохранить"}
        </button>
      </div>
      {saveError && <div role="alert">{saveError}</div>}
      {saved && <div role="status">{saved}</div>}
    </section>
  );
}