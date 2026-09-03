<template>
  <div class="auth-page">
    <div class="auth-shell">
      <aside class="colonne-marque">
        <div class="marque">
          <span class="marque-barre" aria-hidden="true"></span>
          <div>
            <div class="marque-nom">EnerVision</div>
            <div class="marque-baseline">plateforme de suivi énergétique</div>
          </div>
        </div>

        <!-- Motif : la signature graphique des courbes du produit (mesure pleine,
             prédiction en pointillés). Purement décoratif, aucune donnée réelle. -->
        <svg class="trace" viewBox="0 0 520 60" width="100%" height="60" preserveAspectRatio="none" aria-hidden="true">
          <polyline
            points="0,44 52,38 104,27 156,21 208,26 260,34 312,39 364,30 416,19 468,13 520,17"
            fill="none" stroke="#2dd4bf" stroke-width="1.5" opacity="0.6"
          />
          <polyline
            points="0,49 52,45 104,40 156,35 208,35 260,40 312,44 364,39 416,31 468,25 520,23"
            fill="none" stroke="#6b7a99" stroke-width="1" stroke-dasharray="4,4" opacity="0.45"
          />
        </svg>

        <ul class="chaine">
          <li><span>collecte</span><span class="valeur">mesures IoT</span></li>
          <li><span>qualité</span><span class="valeur">bronze → silver → gold</span></li>
          <li><span>restitution</span><span class="valeur">API + interface web</span></li>
        </ul>

        <p class="note">{{ note }}</p>
      </aside>

      <section class="colonne-formulaire">
        <h1 class="titre">{{ title }}</h1>
        <p v-if="subtitle" class="sous-titre">{{ subtitle }}</p>

        <div class="corps">
          <slot />
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: "" },
  note: { type: String, default: "" },
});
</script>

<style scoped>
.auth-shell {
  display: grid;
  grid-template-columns: 1fr 360px;
  width: 100%;
  max-width: 880px;
  border: 1px solid var(--auth-border);
  border-radius: 3px;
  overflow: hidden;
}

.colonne-marque {
  display: flex;
  flex-direction: column;
  gap: 26px;
  padding: 32px;
  background: var(--auth-panel-2);
  border-right: 1px solid var(--auth-border);
}

/* Mêmes filets label/valeur que les listes techniques de la maquette. */
.chaine {
  list-style: none;
}

.chaine li {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 9px 0;
  border-top: 1px solid var(--auth-border);
  font-family: var(--auth-mono);
  font-size: 0.72em;
  color: var(--auth-muted);
}

.chaine .valeur {
  color: var(--auth-text);
  text-align: right;
}

.note {
  margin-top: auto;
}

.marque {
  display: flex;
  gap: 12px;
}

.marque-barre {
  width: 2px;
  align-self: stretch;
  background: var(--auth-accent);
}

.marque-nom {
  font-size: 1.3em;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.marque-baseline {
  margin-top: 3px;
  font-family: var(--auth-mono);
  font-size: 0.72em;
  color: var(--auth-muted);
}

.note {
  font-family: var(--auth-mono);
  font-size: 0.7em;
  line-height: 1.7;
  color: var(--auth-muted);
}

.colonne-formulaire {
  padding: 32px;
  background: var(--auth-panel);
}

.titre {
  font-size: 1.25em;
  font-weight: 600;
}

.sous-titre {
  margin-top: 5px;
  font-family: var(--auth-mono);
  font-size: 0.74em;
  color: var(--auth-muted);
}

.corps {
  margin-top: 26px;
}

@media (max-width: 720px) {
  .auth-shell {
    grid-template-columns: 1fr;
    max-width: 420px;
  }

  .colonne-marque {
    gap: 20px;
    padding: 24px;
    border-right: none;
    border-bottom: 1px solid var(--auth-border);
  }

  .trace {
    display: none;
  }

  .colonne-formulaire {
    padding: 24px;
  }
}
</style>
