module ChattyNotes
  class Template

    attr_accessor :content
    attr_accessor :variables
    attr_accessor :variables_full
    attr_accessor :if_blocks # conditional blocks are not robust

    def initialize(filename)
      file = File.open(filename, 'r:UTF-8')
      @content = file.read
      file.close

      @variables_full = @content.scan /{{.[^}]+}}/ # /{{\s\w+(|)*\s}}/
      @variables = @variables_full.map {|variable| variable.scan(/\w+/).first }
      @if_blocks = @content.scan /{%.+/
    end

    def render(hash)
      output = @content.clone
      variables.each_with_index do |variable, index|
        full = @variables_full[index]
        value = hash[variable]
        value = value.join(', ') if full.include?('join') && value.is_a?(Array)
        output.sub!(full, value.to_s)
      end

      @if_blocks.each_slice(2) do |begin_block,end_block|
        i = output.index(begin_block) + begin_block.length
        j = output.index(end_block) - 1
        optional_content = output[i..j]

        output.sub!(begin_block,'')
        output.sub!(end_block,'')

        if_tokens = begin_block.scan(/\S+/)
        if if_tokens[1] == 'if'
          if_condition = if_tokens[2]
          if_variable = if_condition[0..if_condition.index('|')-1]
          # we're going to just assume the conditional passes if the variable is present
          if hash[if_variable]
            # leave content in
          else
            output.sub!(optional_content,'')
          end
        else
          puts "Error: Unrecognized function: #{begin_block}"
          output.sub!(optional_content,'')
        end
      end
 
      output
    end
  end
end