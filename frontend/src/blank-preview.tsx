// Предпросмотр печати бланка в редакторе справочника (МАКЕТ, не документ):
// показывает, как соберётся печатный лист — шапка, блоки шагов, подвал — с теми
// плейсхолдерами, значения которых известны на клиенте. Настоящий документ
// собирает сервер при печати заявки (python-docx), поэтому макет подписан как
// приблизительный: начертания, кегль, переносы, разбивку по страницам, QR и
// подпись страницы он не воспроизводит, сверять его с листом нельзя.
// Разметку редактора показываем текстом (без dangerouslySetInnerHTML): печать
// всё равно санирует её по своим правилам, а в бланке значение выглядит именно
// текстом. Плейсхолдеры подставляются тем же набором, что печать
// (docs.blank_placeholders): известное значение — текстом, неизвестное (ФИО,
// должность, предприятие, руководитель — их в справочнике бланков нет)
// остаётся текстом, как и на настоящем бланке.
import { richTextHtmlToLines, richTextLinesToHtml, richTextToPlain } from "./rich-text";

// Шаг бланка в макете: то, что печать берёт из состава (снимок заявки).
export interface BlankPreviewStep {
  // Название шага (пустое — печатает «шаг без названия»).
  title: string;
  // Текст шага — список строк (HTML абзаца или простой текст).
  stageLines: string[];
  // Режим шага: parallel — «параллельно», иначе — «последовательно».
  approvalMode: "sequential" | "parallel" | null;
  // Вид исполнителя (определяет, чем заполнена строка «Ответственный»).
  executorKind: "people" | "ad_group" | "manager_ad";
  // Логины согласующих (ФИО подставляет сервер при печати).
  assignees: string[];
  // Группа AD (печатается как есть).
  ownerGroup: string | null;
}

interface BlankPreviewProps {
  // Название бланка (подставляется в {blank_name}).
  blankName: string;
  // Шапка бланка (HTML редактора или пусто).
  headerHtml: string;
  // Подвал бланка — строки печати (HTML абзаца или простой текст).
  footerLines: string[];
  // Шаги бланка в порядке печати.
  steps: BlankPreviewStep[];
}

// Плейсхолдер печати {имя} (контракт docs.blank_placeholders). Двойных скобок
// ({{ ... }} из шаблонов писем) в бланках не бывает.
const PREVIEW_PLACEHOLDER_RE = /\{\s*(\w+)\s*\}/g;

// Плейсхолдер QR в макете: печать вставляет картинку (docs.QR_PLACEHOLDER), в
// макете показываем подпись на её месте — иначе админ видит "{qr}".
const QR_PREVIEW_MARK = "[QR-код]";

// Выравнивание абзаца редактора (style — печать читает только text-align).
const PREVIEW_ALIGN_RE = /text-align\s*:\s*(center|right|left|justify)/i;
// Классы темы под выравнивание; левое и по ширине печатает как есть (default).
const PREVIEW_ALIGN_CLASSES: Record<string, string> = {
  center: "sed-preview__line--center",
  right: "sed-preview__line--right",
};
// Правило печати подвала — по центру (редактор им может переопределить).
const PREVIEW_FOOTER_ALIGN = "sed-preview__line--center";

// Абзац макета: текст для показа и класс выравнивания ("" — как в бланке).
interface PreviewLine {
  text: string;
  align: string;
}

// Значения плейсхолдеров, известные в редакторе бланка: название бланка, счётчик
// шагов и сегодняшняя дата ({date} на бланке — дата печати, её поставит сервер,
// но формат тот же). Остальные значения знает только заявка — их на клиенте нет,
// поэтому в макете остаются плейсхолдеры.
function previewValues(blankName: string, stepCount: number): Record<string, string> {
  const now = new Date();
  const day = String(now.getDate()).padStart(2, "0");
  const month = String(now.getMonth() + 1).padStart(2, "0");
  return {
    blank_name: blankName,
    steps: `количество шагов: ${stepCount}`,
    date: `${day}.${month}.${now.getFullYear()}`,
  };
}

// Подстановка значения плейсхолдера; неизвестное имя остаётся текстом (как печать).
function substitute(text: string, values: Record<string, string>): string {
  return text.replace(PREVIEW_PLACEHOLDER_RE, (match, name: string) => values[name] ?? match);
}

// Строка бланка → абзацы макета: тем же делением, что печать (абзацы, <br>), но
// текстом; выравнивание редактора переносим классом темы.
function previewLines(html: string, values: Record<string, string>): PreviewLine[] {
  return richTextHtmlToLines(richTextLinesToHtml([html]))
    .map((line) => {
      const match = PREVIEW_ALIGN_RE.exec(line);
      return {
        text: substitute(richTextToPlain(line), values),
        align: match ? (PREVIEW_ALIGN_CLASSES[match[1].toLowerCase()] ?? "") : "",
      };
    })
    .filter((line) => line.text !== "");
}

// Список строк бланка (текст шага, подвал) → абзацы макета.
function previewBlock(
  lines: string[],
  values: Record<string, string>,
  fallbackAlign = "",
): PreviewLine[] {
  return lines
    .flatMap((line) => previewLines(line, values))
    .map((item) => ({ ...item, align: item.align || fallbackAlign }));
}

// Абзацы макета одним списком: текст значения и класс выравнивания.
function PreviewParagraphs(props: { lines: PreviewLine[] }) {
  return (
    <>
      {props.lines.map((line, index) => (
        <div key={index} className={line.align}>
          <span className="sed-preview__text">{line.text}</span>
        </div>
      ))}
    </>
  );
}

// Ответственный шага в макете: логины согласующих (ФИО подставит сервер),
// группа AD как есть, руководитель сотрудника — ФИО знает только сервер.
function responsibleText(step: BlankPreviewStep): string {
  if (step.executorKind === "ad_group") return step.ownerGroup ?? "группа AD не выбрана";
  if (step.executorKind === "manager_ad") return "руководитель сотрудника";
  return step.assignees.join(", ");
}

export function BlankPreview(props: BlankPreviewProps) {
  const { blankName, headerHtml, footerLines, steps } = props;
  const values = previewValues(blankName, steps.length);
  // {qr} печатается картинкой ТОЛЬКО в шапке (docs._add_blank_header_html),
  // поэтому подстановка [QR-код] ограничена шапкой: в подвале и тексте шага
  // печать оставит {qr} как есть.
  const header = previewLines(headerHtml, { ...values, qr: QR_PREVIEW_MARK });
  const footer = previewBlock(footerLines, values, PREVIEW_FOOTER_ALIGN);
  return (
    <div className="sed-preview" aria-label="Предпросмотр печати бланка">
      <div className="sed-note">
        Макет приблизителен: настоящий документ печати собирает сервер при печати
        заявки. Здесь видны состав, текст и порядок блоков; начертания, кегль,
        переносы строк, разбивка по страницам, картинка QR (на её месте подпись
        [QR-код]) и подпись страницы не воспроизводятся — сверять макет с листом
        нельзя. Значения плейсхолдеров подставляет сервер: показаны только те,
        что известны здесь (название бланка, количество шагов, дата), остальные
        остаются текстом.
      </div>
      <div className="sed-preview__sheet">
        <div className="sed-preview__block">
          <div className="sed-sub">Шапка</div>
          {header.length === 0 ? (
            <div className="sed-preview__text">
              Шапка не задана — печатаются название бланка и поля сотрудника.
            </div>
          ) : (
            <PreviewParagraphs lines={header} />
          )}
        </div>
        <div className="sed-preview__block">
          <div className="sed-sub">Шаги ({steps.length})</div>
          {steps.length === 0 ? (
            <div className="sed-preview__text">Шагов нет — бланк без шагов выбрать нельзя.</div>
          ) : (
            steps.map((step, index) => {
              const body = previewBlock(step.stageLines, values);
              return (
                <div key={index} className="sed-preview__step">
                  <div className="sed-preview__heading">
                    {index + 1}. {substitute(step.title, values) || "шаг без названия"} (
                    {step.approvalMode === "parallel" ? "параллельно" : "последовательно"})
                  </div>
                  {body.length === 0 ? (
                    <div className="sed-preview__text">Текст шага не задан.</div>
                  ) : (
                    <PreviewParagraphs lines={body} />
                  )}
                  <div>
                    <b>Ответственный:</b>{" "}
                    <span className="sed-preview__text">{responsibleText(step)}</span>
                  </div>
                  {step.executorKind === "people" && step.assignees.length > 0 && (
                    <div className="sed-preview__hint">
                      логины AD; ФИО подставляет сервер при печати
                    </div>
                  )}
                  <div>
                    <b>Отметка/дата:</b> <span className="sed-preview__text">__________</span>
                  </div>
                </div>
              );
            })
          )}
        </div>
        <div className="sed-preview__block">
          <div className="sed-sub">Подвал</div>
          {footer.length === 0 ? (
            <div className="sed-preview__text">Строк подвала нет.</div>
          ) : (
            <PreviewParagraphs lines={footer} />
          )}
        </div>
        <div className="sed-preview__hint">
          Номер заявки, название бланка и его версия печатаются в подписи страницы —
          макетом это не показано. QR печатается на месте плейсхолдера {"{qr}"} в
          шапке; без него QR печатается отдельным блоком сразу после шапки.
        </div>
      </div>
    </div>
  );
}