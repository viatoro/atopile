export default {
  root: ".",
  esbuild: {
    jsx: "automatic",
  },
  server: {
    host: "0.0.0.0",
    port: 5199,
    proxy: {
      "/atopile-ui": {
        target: "http://127.0.0.1:18730",
        ws: true,
      },
    },
  },
};
