# chatty-notes
A tool for generating clinical notes using Synthea patient FHIR Bundles.

**This is a work in progress** - Output is to the terminal for now. Eventual goal
is to create a modified FHIR Bundle with the notes as DocumentReferences.

This project contains the `chatty.py` script that will take a FHIR Bundle as
input. It then scans the FHIR Bundle for encounters. Given information on the
encounter, pulled from other resources in the bundle, it will create a prompt
for ChatGPT. The script uses [OpenAI Chat completions API](https://platform.openai.com/docs/guides/chat) to supply the prompt and get a response.

## Installation

```
git clone https://github.com/synthetichealth/chatty-notes.git
cd chatty-notes/
pip install -r requirements.txt
```

## Running

Running the script requires setting up an account with OpenAI and getting an [API key](https://platform.openai.com/account/api-keys). The API key should be added to the environment in the
`OPENAI_API_KEY` variable.

```
export OPENAI_API_KEY=YOUR_KEY_HERE
python chatty.py -b PATH_TO_FHIR_BUNDLE
```

## License
Copyright 2023 The MITRE Corporation

Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the License. You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.
