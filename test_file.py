from agents import Agent, function_tool
from google import genai
from google.genai import types
from PIL import Image
from io import BytesIO

@function_tool
def generate_image(prompt: str, image_path: str | None = None) -> str:
    """Generate or edit an image using Google Gemini's 'Nano Banana' model.
    
    Args:
        prompt: A textual description of the image to generate, or the edit to perform.
        image_path: Optional path to an input image file to edit. If provided, 
                   the prompt will be applied as an edit to this image.
    
    Returns:
        A message indicating where the generated image is saved.
    """
    # Initialize Gemini client (uses GEMINI_API_KEY from environment)
    client = genai.Client()
    
    # Prepare the content inputs for the API call
    if image_path:
        # Open the input image for editing
        base_image = Image.open(image_path)
        contents = [prompt, base_image]  # text + image input for editing
    else:
        contents = [prompt]  # text-only input for generation
    
    # Call the Gemini image generation model (Nano Banana)
    response = client.models.generate_content(
        model="gemini-2.5-flash-image-preview",  # Latest image model ID
        contents=contents
    )
    
    # Extract image data from the response
    output_image = None
    for part in response.candidates[0].content.parts:
        if part.inline_data is not None:
            # Get the generated image bytes
            image_bytes = part.inline_data.data
            output_image = Image.open(BytesIO(image_bytes))
    
    # Save the output image to a file
    output_path = "gemini_result.png"
    if output_image:
        output_image.save(output_path)
        return f"Image generated and saved to {output_path}"
    else:
        return "No image was generated."