# ChatSphere Premium

A high-performance, real-time collaboration and chatting platform built with a dark spatial "Bento-Box" user interface design. ChatSphere features multiple user direct messaging, group creations, real-time WhatsApp-style message deletion, rich media/file attachments, profile bios, automated animated avatar generation, and custom popup view-once message destruction.

The architecture is highly optimized for performance and network stability by running entirely on a single Python FastAPI backend with persistent SQLite database tracking.

## Key Features
- **Persistent Local Database:** All user identities, room configurations, and chat histories are permanently saved in a local database (`chatsphere.db`).
- **Direct Messaging & Group Frameworks:** Launch fluid DMs or spin up complex group chats using a user-focused global WebSocket pipeline.
- **Rich Media Sharing:** Attach images and documents natively into chat rooms using the 📎 attachment mechanism.
- **Secure View-Once Popups:** View-once messages safely launch within an isolated modal pop-up overlay window. Shutting the window fires an instantaneous server deletion query, completely wiping it from all displays.
- **WhatsApp-Style Deletion:** Hover over any message you've sent and click 🗑️ to delete it for everyone across the entire network instantly.
- **Profile Customization Suite:** Modify your display name, edit profile bios, upload local file photos, or compile animated avatar variants using the integrated generator.

## Quick Start Launcher (Windows Deployment)

The project includes an automated single-click launcher that configures your virtual environment, installs required dependencies, and starts all services in the background.

1. Open your File Explorer or Command Prompt (CMD).
2. Direct yourself into the project path directory: `D:\Real Time Chatting`
3. Execute the custom launcher:
   ```cmd
   start-all.bat