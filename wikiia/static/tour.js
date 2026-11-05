/* Tour guiado de WikiIA: se abre solo en la primera visita e ilumina cada parte de la pantalla.
 * app.py lo inyecta junto con driver.js (wikiia/static/driverjs) y llama a __wikiiaIniciarTour(cfg):
 *   cfg.cookie: nombre de la cookie que recuerda que ya se vio (1 año)
 *   cfg.pasos:  [{el: selector CSS o null, titulo, texto (HTML), barra: true/false/null, lado}]
 *               barra=true necesita la barra lateral abierta; false, cerrada en el celular.
 */
window.__wikiiaIniciarTour = function (cfg) {
  var visto = document.cookie.split(/;\s*/).indexOf(cfg.cookie + "=1") !== -1;
  if (visto || window.__wikiiaTourActivo) return;
  window.__wikiiaTourActivo = true;

  var movil = function () { return window.innerWidth < 768; };
  var barra = function () { return document.querySelector('[data-testid="stSidebar"]'); };
  var barraAbierta = function () {
    var b = barra();
    if (!b) return false;
    var caja = b.getBoundingClientRect();
    return b.getAttribute("aria-expanded") !== "false" && caja.width > 60 && caja.right > 60;
  };
  // Abre o cierra la barra lateral con los botones de Streamlit. Devuelve true si hubo que moverla.
  var moverBarra = function (abrir) {
    if (abrir === barraAbierta()) return false;
    var boton = abrir
      ? document.querySelector('[data-testid="stExpandSidebarButton"]')
      : document.querySelector('[data-testid="stSidebarCollapseButton"] button');
    if (!boton) return false;
    boton.click();
    return true;
  };

  var iniciar = function () {
    document.cookie = cfg.cookie + "=1; max-age=31536000; path=/; SameSite=Lax";
    var pasos = cfg.pasos.filter(function (p) { return !p.el || document.querySelector(p.el); });
    var tour;
    // Antes de cada paso acomoda la barra lateral y espera su animación.
    var ir = function (i) {
      if (i < 0) return;
      if (i >= pasos.length) { tour.destroy(); return; }
      var p = pasos[i];
      var movio = p.barra === true ? moverBarra(true) : (p.barra === false && movil() ? moverBarra(false) : false);
      setTimeout(function () { tour.moveTo(i); }, movio ? 450 : 0);
    };
    tour = window.driver.js.driver({
      showProgress: true,
      progressText: "{{current}} de {{total}}",
      nextBtnText: "Siguiente →",
      prevBtnText: "← Atrás",
      doneBtnText: "¡Empezar!",
      popoverClass: "wikiia-tour",
      overlayOpacity: 0.72,
      stagePadding: 6,
      stageRadius: 10,
      smoothScroll: true,
      steps: pasos.map(function (p) {
        var paso = { popover: { title: p.titulo, description: p.texto } };
        if (p.el) {
          paso.element = p.el;
          paso.popover.side = p.lado || "bottom";
          paso.popover.align = "start";
        }
        return paso;
      }),
      onNextClick: function () { ir(tour.getActiveIndex() + 1); },
      onPrevClick: function () { ir(tour.getActiveIndex() - 1); },
      onDestroyed: function () {
        window.__wikiiaTourActivo = false;
        if (movil()) moverBarra(false);
      },
    });
    tour.drive(0);
  };

  // Espera a que Streamlit termine de dibujar los filtros y las pestañas (máx. 12 s).
  var intentos = 0;
  (function esperar() {
    var listo = document.querySelector('[data-testid="stTabs"]') && document.querySelector(".st-key-f_buscar");
    if (listo) { setTimeout(iniciar, 700); return; }
    if (intentos++ < 60) { setTimeout(esperar, 200); return; }
    window.__wikiiaTourActivo = false;
  })();
};
