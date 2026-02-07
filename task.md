Create a new cross platform, voice keyboard app. This virtual keyboard should use OpenAI gpt-4o-mini-transcribe model to transcribe voice to text and then type the text into the current input field.

Create a single software/library that handles the voice input, transcribing, and the virtual keyboard functionality across all platforms (Windows, macOS, Linux).
It should be packaged into a single executable file that works on both platforms easily, without requiring users to install Python or dependencies.
Make a simple UI that runs in the background, listens to global shortcuts to activate the transctiotion, and allows users to set up OpenAI API key.
The modern UI should have config options for shortcuts, and have a big round start/stop button (so it can be started using a button). The configured shourcut should listen on the whole system, not just the current application. The two shortcuts are for toggle and push to talk mode.

Before you start the task, please organize the requests into a detailed plan.