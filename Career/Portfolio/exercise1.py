import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv() #loads secret keys from .env

client = OpenAI() #initiates OpenAI client
model = "gpt-5.6-luna" #sets the model to be used

messages = [] #used to maintain context throughout the conversation. For now, this is simply a record of prompts and responses.

while True: #Simple loop that lets me keep prompting 
    exit_phrases = ["quit", "exit", "goodbye", "bye", "done"]
    user_input = input("You: ") #get user input
    
    if user_input.lower() in exit_phrases: #checks to see if the user wants to exit
        break
    
    messages.append({"role": "user", "content": user_input}) #appends user input to the list of messages

    output = client.responses.create(model = model, input = messages) #calls the API to get the response
    print("Agent: ", output.output_text) #prints the response

    messages.append({"role": "assistant", "content": output.output_text}) #appends assistant response to the list of messages
