// Окна-попы (Задача 3): построение URL и открытие окна ПО КЛИКУ пользователя
// (иначе браузер блокирует popup). Токен в localStorage (sed_token) общий для
// origin — окно открывается авторизованным, ничего менять не надо.

// URL карточки заявки (?view=request&id=…).
export function requestUrl(id: string): string {
  return `?view=request&id=${encodeURIComponent(id)}`;
}

// URL карточки сотрудника (?view=employee&key=enterprise|base_code|tab_num).
export function employeeUrl(key: string): string {
  return `?view=employee&key=${encodeURIComponent(key)}`;
}

// URL формы создания заявки (?view=create).
export function createUrl(): string {
  return "?view=create";
}

// Открыть окно-попу: параметры браузера для отдельного окна.
export function openPopup(url: string, width = 940, height = 720): void {
  window.open(url, "_blank", `popup,width=${width},height=${height},resizable=yes`);
}