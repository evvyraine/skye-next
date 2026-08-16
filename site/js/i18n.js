export const strings = {
  ru: {
    skip: "К содержанию",
    bot: "бот",
    docs: "документация",
    blog: "блог",
    lang: "en",
    langTo: "en",
    theme: "Тема",
    sound: "Звук",
    menu: "Меню",
    heroLine1: "Спокойная",
    heroLine2: "сторона ИИ",
    lede: "Личный ассистент внутри Telegram, который понимает, помнит, создаёт и действует — без лишнего шума.",
    start: "Начать разговор",
    introTitle: "Пишите\nкак обычно",
    introBody:
      "Skye живёт там, где уже проходят ваши разговоры, работа и повседневные дела. Не нужно осваивать ещё один сервис — просто напишите ей так, как написали бы человеку. Коротко, голосом, с фотографией или документом — Skye разберётся в контексте и поможет довести дело до результата.",
    introAside:
      "Это российская разработка с открытым исходным кодом. Модели искусственного интеллекта предоставляются внешними партнёрами.",
    originKicker: "О проекте",
    originRu: "Российская разработка",
    originOs: "Открытый исходный код",
    originAi: "Модели — от партнёров",
    repo: "Репозиторий проекта",
    f1Title: "Общайтесь текстом и голосом",
    f1Body:
      "Получайте ответы на вопросы, проводите мозговые штурмы и отправляйте голосовые сообщения. Skye объяснит суть — и раскроет тему, когда нужно. А ещё ассистент работает в группах и помогает вести каналы.",
    f2Title: "Работайте с фото и документами",
    f2Body:
      "Создавайте и изменяйте изображения — просто скажите, что вы хотите получить. Skye опишет картинку, разберёт и сравнит документы, объяснит данные в таблице и даст краткое содержание текста.",
    f3Title: "Ищите информацию и исследуйте темы",
    f3Body:
      "Собирайте факты из разных источников, получайте актуальную информацию и экспериментируйте. Skye не только советует, но и действует в подключённых сервисах: Gmail, Github, Notion, Slack.",
    f4Title: "Создавайте личных агентов",
    f4Body:
      "Организуйте собственную команду агентов, каждый из которых имеет свой характер и специализируется на предметной области. Skye сама переключится между нужными моделями и учтёт все инструкции.",
    pricingTitle: "Всего\nдва тарифа",
    freeName: "Free",
    freePrice: "0 ₽",
    perMonth: "в месяц",
    free1: "100 тыс. токенов",
    free2: "Базовые возможности",
    plusName: "Plus",
    plusPrice: "990 ₽",
    plus1: "2 млн токенов",
    plus2: "Все возможности",
    plus3: "Выбор модели",
    plus4: "Дополнительные пакеты",
    outro: "Понять важное.\nДовести до результата.\nОстаться человечной.",
    tryNow: "Попробуйте сейчас",
    privacy: "Политика конфиденциальности",
    support: "Поддержка →",
    designed: "Задизайнено",
  },
  en: {
    skip: "Skip to content",
    bot: "bot",
    docs: "docs",
    blog: "blog",
    lang: "ru",
    langTo: "ru",
    theme: "Theme",
    sound: "Sound",
    menu: "Menu",
    heroLine1: "The calm",
    heroLine2: "side of AI",
    lede: "A personal assistant inside Telegram that understands, remembers, creates, and acts — without the noise.",
    start: "Start a conversation",
    introTitle: "Write\nas usual",
    introBody:
      "Skye lives where your conversations, work, and everyday life already happen. There is no extra app to learn — write to her the way you would write to a person. Short, by voice, with a photo or a document: she will pick up the context and help you finish the thing.",
    introAside:
      "This is an open-source project. The models come from external partners.",
    originKicker: "About the project",
    originRu: "Made in Russia",
    originOs: "Open source",
    originAi: "Models from partners",
    repo: "Project repository",
    f1Title: "Talk in text and voice",
    f1Body:
      "Ask questions, think out loud, send a voice note. Skye will give you the point — and go deeper when you need it. She also works in groups and can help run channels.",
    f2Title: "Work with photos and documents",
    f2Body:
      "Create and edit images by saying what you want. Skye can describe a picture, read and compare documents, explain a spreadsheet, and summarize a text.",
    f3Title: "Search and follow a thread",
    f3Body:
      "Gather facts, stay current, and try things. Skye does not only advise — she can act in connected services: Gmail, GitHub, Notion, Slack.",
    f4Title: "Make your own agents",
    f4Body:
      "Build a small team, each with a character and a specialty. Skye will switch models and keep every instruction in view.",
    pricingTitle: "Just\ntwo plans",
    freeName: "Free",
    freePrice: "0 ₽",
    perMonth: "per month",
    free1: "100k tokens",
    free2: "Core capabilities",
    plusName: "Plus",
    plusPrice: "990 ₽",
    plus1: "2M tokens",
    plus2: "Everything included",
    plus3: "Model choice",
    plus4: "Extra packs",
    outro: "See what matters.\nFollow through.\nStay human.",
    tryNow: "Try it now",
    privacy: "Privacy policy",
    support: "Support →",
    designed: "Designed by",
  },
};

export function currentLang() {
  const stored = localStorage.getItem("skye-lang");
  if (stored === "en" || stored === "ru") return stored;
  return "ru";
}

export function t(key) {
  const lang = currentLang();
  return strings[lang][key] ?? strings.ru[key] ?? key;
}

export function applyI18n(root = document) {
  const lang = currentLang();
  document.documentElement.lang = lang;
  const dict = strings[lang];
  root.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    const value = dict[key];
    if (value == null) return;
    if (el.hasAttribute("data-i18n-html")) {
      el.innerHTML = value.replaceAll("\n", "<br>");
      return;
    }
    el.textContent = value.includes("\n") ? value.replaceAll("\n", " ") : value;
  });
  root.querySelectorAll("[data-i18n-aria]").forEach((el) => {
    const key = el.getAttribute("data-i18n-aria");
    if (dict[key]) el.setAttribute("aria-label", dict[key]);
  });
  root.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    const key = el.getAttribute("data-i18n-placeholder");
    if (dict[key]) el.setAttribute("placeholder", dict[key]);
  });
}
