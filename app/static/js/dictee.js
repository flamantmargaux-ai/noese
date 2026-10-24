/* Dictée : enregistre au micro, convertit en WAV 16 kHz mono dans le navigateur,
   envoie au serveur qui transcrit avec le modèle configuré (hébergé dans l'UE). */
(function () {
  "use strict";

  // Formulaires longs à traiter : on l'indique sur le bouton.
  document.querySelectorAll("form[data-attente]").forEach(function (form) {
    form.addEventListener("submit", function () {
      var b = form.querySelector("button[type=submit]");
      if (b) { b.disabled = true; b.textContent = form.dataset.attente; }
    });
  });

  var bouton = document.getElementById("dicter");
  if (!bouton) return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) return;
  bouton.hidden = false;

  var statut = document.getElementById("statut-dictee");
  var duree = document.getElementById("duree-dictee");
  var zone = document.getElementById("texte");
  var champDictee = document.getElementById("dictee");
  var libelle = bouton.querySelector(".libelle");
  var enregistreur = null, morceaux = [], flux = null, debut = 0, minuteur = null;

  function arreterMinuteur() { clearInterval(minuteur); duree.textContent = ""; }

  bouton.addEventListener("click", async function () {
    if (enregistreur && enregistreur.state === "recording") { enregistreur.stop(); return; }
    try {
      flux = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      statut.textContent = "Le micro n'est pas accessible. Autorisez-le dans votre navigateur, ou écrivez votre réponse.";
      return;
    }
    morceaux = [];
    enregistreur = new MediaRecorder(flux);
    enregistreur.ondataavailable = function (e) { if (e.data.size) morceaux.push(e.data); };
    enregistreur.onstop = transcrire;
    enregistreur.start();
    debut = Date.now();
    bouton.setAttribute("aria-pressed", "true");
    bouton.classList.add("enregistre");
    libelle.textContent = "Arrêter la dictée";
    statut.textContent = "Enregistrement en cours. Parlez normalement, puis appuyez sur Arrêter.";
    minuteur = setInterval(function () {
      var s = Math.round((Date.now() - debut) / 1000);
      duree.textContent = Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
      if (s >= 300) enregistreur.stop();  // 5 minutes au plus
    }, 500);
  });

  async function transcrire() {
    flux.getTracks().forEach(function (t) { t.stop(); });
    arreterMinuteur();
    bouton.setAttribute("aria-pressed", "false");
    bouton.classList.remove("enregistre");
    libelle.textContent = "Dicter ma réponse";
    bouton.disabled = true;
    statut.textContent = "Transcription en cours…";
    try {
      var wav = await versWav(new Blob(morceaux, { type: enregistreur.mimeType }));
      var donnees = new FormData();
      donnees.append("audio", wav, "dictee.wav");
      var r = await fetch(bouton.dataset.url, { method: "POST", body: donnees });
      var j = await r.json();
      if (!r.ok) throw new Error(j.erreur);
      if (!j.texte) throw new Error("Nous n'avons rien entendu. Réessayez un peu plus près du micro.");
      zone.value = (zone.value.trim() ? zone.value.trim() + " " : "") + j.texte;
      champDictee.value = "1";
      statut.textContent = "Votre dictée est ajoutée à la réponse. Relisez-la, puis mettez en forme.";
      zone.focus();
    } catch (e) {
      statut.textContent = e.message || "La transcription n'a pas fonctionné. Réessayez, ou écrivez votre réponse.";
    } finally {
      bouton.disabled = false;
    }
  }

  async function versWav(blob) {
    var Contexte = window.AudioContext || window.webkitAudioContext;
    var ctx = new Contexte();
    var source = await ctx.decodeAudioData(await blob.arrayBuffer());
    ctx.close();
    var taux = 16000;
    var hors = new OfflineAudioContext(1, Math.ceil(source.duration * taux), taux);
    var lecteur = hors.createBufferSource();
    lecteur.buffer = source;
    lecteur.connect(hors.destination);
    lecteur.start();
    var rendu = await hors.startRendering();
    var echantillons = rendu.getChannelData(0);
    var tampon = new ArrayBuffer(44 + echantillons.length * 2);
    var v = new DataView(tampon);
    function ecrire(o, t) { for (var i = 0; i < t.length; i++) v.setUint8(o + i, t.charCodeAt(i)); }
    ecrire(0, "RIFF"); v.setUint32(4, 36 + echantillons.length * 2, true); ecrire(8, "WAVE");
    ecrire(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, taux, true); v.setUint32(28, taux * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    ecrire(36, "data"); v.setUint32(40, echantillons.length * 2, true);
    for (var i = 0; i < echantillons.length; i++) {
      var x = Math.max(-1, Math.min(1, echantillons[i]));
      v.setInt16(44 + i * 2, x < 0 ? x * 0x8000 : x * 0x7fff, true);
    }
    return new Blob([tampon], { type: "audio/wav" });
  }
})();
