require 'net/http'
require 'json'

module MITRE
  class LLM

    DEFAULT_MODEL = ''
    MODELS = {}

    RETRY_LIMIT = 3

    REQUEST_TEMPLATE = {
      'model' => nil,
      'messages' => [
        {
          'role' => 'system',
          'content' => nil
        },
        {
          'role' => 'user',
          'content' => nil
        }
      ]
    }

    attr_accessor :current_model_name
    attr_accessor :current_model
    attr_accessor :current_uri
    attr_accessor :request
    attr_accessor :response
    attr_accessor :reply

    def initialize(model = DEFAULT_MODEL)
      @current_model_name = model
      @request = REQUEST_TEMPLATE.clone()
      set_model(@current_model_name)
    end

    def set_model(model_name)
      @current_model_name = model_name
      @current_model = MODELS[@current_model_name]
      @current_uri = URI("#{@current_model.first}/v1/chat/completions")
      @request['model'] = @current_model.last
    end

    def set_role(system_role)
      @request['messages'].first['content'] = system_role
    end

    def request(user_prompt)
      @request['messages'].last['content'] = user_prompt
      attempt = 0
      begin
        @response = Net::HTTP.post(@current_uri, @request.to_json, 'Content-Type' => 'application/json')
      rescue => e
        puts e.message
        attempt += 1
        if (attempt >= RETRY_LIMIT)
          sleep((3 * attempt).seconds)
          puts '... retry ...'
          retry
        else
          raise(e)
        end
      end
      json = JSON.parse(@response.body)
      @reply = json['choices'].first['message']['content']
      @reply
    end
  end
end