import js from "@eslint/js";
import pluginVue from "eslint-plugin-vue";

export default [
  js.configs.recommended,
  // "essential" attrape les vraies erreurs Vue (clés dupliquées, effets de bord
  // dans un computed, etc.) sans imposer de style de formatage (multi-attributs
  // par ligne, auto-fermeture...) que le code existant ne suit pas. "recommended"
  // inclut cette couche stylistique en plus — à activer plus tard si l'équipe
  // décide d'adopter ce format, pas en même temps que ce linter.
  ...pluginVue.configs["flat/essential"],
  {
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: {
        // Vite/navigateur : pas de config `env` façon .eslintrc en flat config,
        // on liste directement les globales utilisées par le code du front.
        window: "readonly",
        document: "readonly",
        console: "readonly",
        fetch: "readonly",
        localStorage: "readonly",
        import: "readonly",
      },
    },
    rules: {
      // Beaucoup de composants ont un seul mot dans leur nom de fichier
      // (ex. App.vue) ; pas de convention multi-mots imposée ici.
      "vue/multi-word-component-names": "off",
    },
  },
  {
    ignores: ["dist/**", "node_modules/**"],
  },
];
