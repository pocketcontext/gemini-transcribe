{ config, lib, pkgs, ... }:
{
  packages = [ pkgs.uv ];
  languages.python = {
    enable = true;
    package = pkgs.python312;
  };
  scripts.gemini-transcribe = {
    description = "Transcribe audio with Gemini and speaker labels";
    exec = ''
      exec uv run --locked --project ${lib.escapeShellArg config.devenv.root} python -m gemini_transcribe.cli "$@"
    '';
  };
  enterShell = ''
    echo "Run: gemini-transcribe --help"
  '';
}
