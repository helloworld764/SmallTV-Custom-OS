import {displayOptions,withinSchedule} from '../shared/display-options.mjs';
export function applyOptions(root,settings,now=new Date()) {
  const o=displayOptions(settings.advanced||{}),display=root.querySelector('.display');
  for(const key of ['--screen-bg','--screen-text','--screen-accent'])display.style.removeProperty(key);
  if(o.custom_colors){display.style.setProperty('--screen-bg',o.background);display.style.setProperty('--screen-text',o.text);display.style.setProperty('--screen-accent',o.accent);}
  display.dataset.backdrop=o.backdrop;display.dataset.petMotion=o.pet_motion;
  const night=o.night_enabled&&withinSchedule(now,o.timezone,o.night_start,o.night_end);
  const brightness=night?o.night_brightness:settings.brightness;
  root.querySelector('#screen-content').style.opacity=brightness/100;
  return o;
}
