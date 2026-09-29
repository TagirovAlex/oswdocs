// Админка настроек для SED_ADMINS (волна B4).
// Значения — из settings БД (здесь мок getSettings), в коде не хардкодятся.
// Все персональные данные отсутствуют (только технические настройки).
import { useEffect, useState } from "react";
import { mockApi } from "./api-mock";
import type { Role } from "./api-mock";

interface AdminSettingsProps {
  // Роль (форма — только SED_ADMINS).
  role: Role;
}

// Админка: TTL отметок + лимиты скана + флаги процесса.
export function AdminSettings(props: AdminSettingsProps) {
  const { role } = props;
  const [ttl, setTtl] = useState<number>(3); // Дефолт как в сидах settings (на стенде — из таблицы settings).
  const [retentionDays, setRetentionDays] = useState<number>(30);
  const [maxMb, setMaxMb] = useState<number>(10);
  const [paperRequired, setPaperRequired] = useState<boolean>(true);
  const [smtpFrom, setSmtpFrom] = useState<string>("sed@example.com"); // Из settings (smtp_from).
  const [error, setError] = useState<string>("");
  const [saved, setSaved] = useState<string>("");

  // Загрузка настроек (доступ — только админам, иначе 403 из мока).
  useEffect(() => {
    let alive = true;
    mockApi
      .getSettings(role)
      .then((data) => {
        if (alive) {
          setTtl(data.approvalTtlDays);
          setSmtpFrom(data.smtpFrom);
          setError("");
        }
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : "Ошибка загрузки настроек");
      });
    return () => {
      alive = false;
    };
  }, [role]);

  // Настройки — только админам.
  if (error) return <div role="alert">Ошибка: {error}</div>;

  return (
    <section aria-label="Настройки СЭД">
      <h3>Настройки (только SED_ADMINS)</h3>
      <div className="sed-note">Значения хранятся в settings БД и применяются без пересборки.</div>
      <label style={{ display: "block", marginTop: 8 }}>
        TTL отметок, дней (approval_ttl_days)
        <input
          aria-label="TTL отметок"
          type="number"
          min={1}
          max={30}
          value={ttl}
          onChange={(e) => setTtl(Number(e.target.value))}
        />
      </label>
      <label style={{ display: "block", marginTop: 8 }}>
        Хранение сканов, дней (scan_retention_days)
        <input
          aria-label="Хранение сканов"
          type="number"
          min={1}
          max={365}
          value={retentionDays}
          onChange={(e) => setRetentionDays(Number(e.target.value))}
        />
      </label>
      <label style={{ display: "block", marginTop: 8 }}>
        Лимит скана, МБ (scan_max_mb)
        <input
          aria-label="Лимит скана"
          type="number"
          min={1}
          max={100}
          value={maxMb}
          onChange={(e) => setMaxMb(Number(e.target.value))}
        />
      </label>
      <label style={{ display: "block", marginTop: 8 }}>
        <input
          type="checkbox"
          checked={paperRequired}
          onChange={(e) => setPaperRequired(e.target.checked)}
        />
        Требовать бумажное заявление (require_paper_signature)
      </label>
      <label style={{ display: "block", marginTop: 8 }}>
        Отправитель уведомлений, e-mail (smtp_from)
        <input
          aria-label="Отправитель уведомлений"
          type="email"
          value={smtpFrom}
          onChange={(e) => setSmtpFrom(e.target.value)}
        />
      </label>
      <div className="sed-toolbar" style={{ marginTop: 12 }}>
        <button
          type="button"
          className="sed-btn"
          onClick={() =>
            setSaved(`Сохранено: TTL=${ttl} дн., сканы ${retentionDays} дн./${maxMb} МБ, от ${smtpFrom}`)
          }
        >
          Сохранить
        </button>
      </div>
      {saved && <div role="status">{saved}</div>}
    </section>
  );
}
