# Contributing to YouTube Music Downloader

Thank you for considering contributing to this project! 🎵

## How to Contribute

### Reporting Bugs

If you find a bug, please open an issue with:
- A clear, descriptive title
- Steps to reproduce the issue
- Expected behavior vs actual behavior
- Your environment (OS, Docker version, etc.)
- Relevant logs or screenshots

### Suggesting Features

Feature suggestions are welcome! Please open an issue with:
- A clear description of the feature
- Why this feature would be useful
- Any implementation ideas you might have

### Pull Requests

1. **Fork the repository** and create your branch from `main`
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes**
   - Follow the existing code style
   - Add comments for complex logic
   - Update documentation if needed

3. **Test your changes**
   ```bash
   docker-compose down
   docker-compose up --build
   ```

4. **Commit your changes**
   - Use clear, descriptive commit messages
   - Reference any related issues

5. **Push to your fork and submit a pull request**

## Development Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/AJZander/youtube-music-downloader.git
   cd youtube-music-downloader
   ```

2. Copy environment files:
   ```bash
   cp backend/.env.example backend/.env
   cp frontend/.env.example frontend/.env
   ```

3. Start the development environment:
   ```bash
   docker-compose up --build
   ```

## Code Style

### Python (Backend)
- Follow PEP 8 guidelines
- Use type hints where appropriate
- Add docstrings to functions and classes
- Use async/await for I/O operations

### JavaScript (Frontend)
- Use functional components with hooks
- Follow React best practices
- Use meaningful variable names
- Add comments for complex logic

## Project Structure

```
youtube-music-downloader/
├── backend/          # FastAPI backend
│   └── app/         # Application code
├── frontend/        # React frontend
│   └── src/         # Source code
└── docker-compose.yml
```

## Testing

Currently, the project doesn't have automated tests. Adding tests would be a great contribution!

## Questions?

Feel free to open an issue for any questions or clarifications.

## Code of Conduct

- Be respectful and inclusive
- Provide constructive feedback
- Focus on what is best for the community
- Show empathy towards others

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
