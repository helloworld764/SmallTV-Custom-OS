// Draws approved public pet state sheets in the simulator. The sheets remain
// untouched; frame boundaries are an even 3x3 preview grid, not firmware data.
export const PET_SHEETS = Object.freeze({
  Pixel: Object.freeze({ src: './pets/Pixel-states.png', columns: 3, rows: 3 }),
  Byte: Object.freeze({ src: './pets/Byte-states.png', columns: 3, rows: 3 }),
  Glitch: Object.freeze({ src: './pets/Glitch-states.png', columns: 3, rows: 3 }),
});

export const PET_STATES = Object.freeze([
  'neutral', 'happy', 'eating', 'playing', 'sleeping', 'waking',
  'hungry_sad', 'tired', 'blink',
]);

const REACTION_STATE = Object.freeze({
  ADOPT: 'happy', FEED: 'eating', PLAY: 'playing',
  SLEEP: 'sleeping', WAKE: 'waking', RETURN: 'happy',
});
const EMOTION_STATE = Object.freeze({
  content: 'neutral', hungry: 'hungry_sad', tired: 'tired',
  sad: 'hungry_sad', sleepy: 'sleeping', missing: 'hungry_sad', unknown: 'neutral',
});

export function petPresentation(pet, nowSeconds) {
  if (!pet || !PET_SHEETS[pet.kind]) return { state: 'neutral', available: false, label: 'Питомец недоступен' };
  if (pet.lifecycle !== 'ACTIVE') {
    return {
      state: EMOTION_STATE[pet.emotion] || 'hungry_sad',
      available: true,
      label: pet.lifecycle === 'RETURN_AVAILABLE' ? 'Можно начать заново'
        : pet.lifecycle === 'ABSENT' ? 'Питомец пропал' : 'Состояние пока неизвестно',
      lifecycle: pet.lifecycle,
    };
  }

  const reaction = pet.reaction;
  if (reaction && Number.isSafeInteger(reaction.expires_at) && reaction.expires_at > nowSeconds) {
    const state = REACTION_STATE[reaction.kind];
    if (state) return { state, available: true, label: actionLabel(reaction.kind), reactionId: reaction.id };
  }
  const state = EMOTION_STATE[pet.emotion] || 'neutral';
  return { state, available: true, label: emotionLabel(pet.emotion) };
}

export function drawPetFrame(context, image, character, state, width, height) {
  const sheet = PET_SHEETS[character];
  if (!sheet || !image?.naturalWidth || !image?.naturalHeight
      || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return false;
  const stateIndex = PET_STATES.indexOf(state);
  const index = stateIndex < 0 ? 0 : stateIndex;
  const sourceWidth = image.naturalWidth / sheet.columns;
  const sourceHeight = image.naturalHeight / sheet.rows;
  const sourceX = (index % sheet.columns) * sourceWidth;
  const sourceY = Math.floor(index / sheet.columns) * sourceHeight;
  const scale = Math.min(width / sourceWidth, height / sourceHeight);
  const drawWidth = sourceWidth * scale;
  const drawHeight = sourceHeight * scale;
  context.clearRect(0, 0, width, height);
  context.imageSmoothingEnabled = false;
  context.drawImage(image, sourceX, sourceY, sourceWidth, sourceHeight,
    (width - drawWidth) / 2, (height - drawHeight) / 2, drawWidth, drawHeight);
  return true;
}

function actionLabel(kind) {
  return ({ ADOPT: 'Рад встрече!', FEED: 'Вкусно!', PLAY: 'Здорово играем!',
    SLEEP: 'Сладких снов', WAKE: 'Доброе утро!', RETURN: 'Начинаем снова!' })[kind] || '';
}

function emotionLabel(emotion) {
  return ({ content: 'Всё хорошо', hungry: 'Хочется перекусить', tired: 'Пора отдохнуть',
    sad: 'Немного грустно', sleepy: 'Хочется спать', missing: 'Питомец скучает',
    unknown: 'Состояние пока неизвестно' })[emotion] || 'Смотрит на тебя';
}
