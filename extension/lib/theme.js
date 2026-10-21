/* Follow the browser's light/dark preference (tokens.css is dark by default). Loaded in <head>
   as a classic script so the page never flashes the wrong theme. */
(function () {
  const mq = window.matchMedia('(prefers-color-scheme: light)');
  const apply = () => document.documentElement.setAttribute('data-theme', mq.matches ? 'light' : 'dark');
  apply();
  mq.addEventListener('change', apply);
})();
