require 'net/http'
require 'json'
require_relative 'ruby/template'
require_relative 'ruby/llm'

context = {}
context['name'] = 'John Doe'
context['age'] = 55
context['race'] = 'white'
context['gender'] = 'male'
context['reason'] = 'malaise'
context['medications'] = ['aspirin', 'ibuprofen']
context['procedures'] = ['physical exam', 'cognitive assessment']
context['procedure'] = 'physical therapy'
# context['immunizations'] = nil
# context['encounter_type'] = nil
# context['conditions'] = nil

# template = ChattyNotes::Template.new('./templates/death_certification.txt.jinja')
template = ChattyNotes::Template.new('./templates/emergency_room.txt.jinja')
# template = ChattyNotes::Template.new('./templates/encounter_for_problem.txt.jinja')
# template = ChattyNotes::Template.new('./templates/oa_encounter.txt.jinja')
prompt = template.render(context)

# ---------------------------------------

llm = MITRE::LLM.new('llama4')
llm.set_role('You are a licensed clinician responsible for writing patient clinical notes.')
reply = llm.request(prompt)
puts reply

puts 'Done.'
