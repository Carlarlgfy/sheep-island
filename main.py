import pygame
import sys
import random

from mapgen import MapGenerator, WATER, SAND, DIRT, GRASS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCREEN_W, SCREEN_H = 1280, 720
FPS = 60

MAP_W, MAP_H = 160, 160

TILE_SIZE_DEFAULT = 14.0
TILE_SIZE_MIN     = 4.0
TILE_SIZE_MAX     = 48.0
ZOOM_FACTOR       = 1.1   # multiplicative zoom per step
CAMERA_SPEED      = 4

TILE_COLORS = {
    WATER: (45,  110, 190),
    SAND:  (210, 190, 130),
    DIRT:  (139, 100,  60),
    GRASS: ( 75, 155,  55),
}

STATE_TITLE = "title"
STATE_MAP   = "map"


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def draw_button(surface, btn, font):
    pygame.draw.rect(surface, btn["color"], btn["rect"], border_radius=8)
    label = font.render(btn["label"], True, (255, 255, 255))
    surface.blit(label, label.get_rect(center=btn["rect"].center))


def draw_title(screen, font_title, font_ui, buttons):
    screen.fill((18, 18, 36))

    title_surf = font_title.render("Sheep Island", True, (240, 215, 90))
    screen.blit(title_surf, title_surf.get_rect(center=(SCREEN_W // 2, SCREEN_H // 3)))

    sub = font_ui.render("a procedural island simulator", True, (140, 140, 180))
    screen.blit(sub, sub.get_rect(center=(SCREEN_W // 2, SCREEN_H // 3 + 64)))

    for btn in buttons:
        draw_button(screen, btn, font_ui)


def draw_map(screen, grid, tile_size, cam_x, cam_y):
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    ts   = max(1, round(tile_size))
    cx   = int(cam_x)
    cy   = int(cam_y)

    # Only draw tiles that are actually on screen
    start_col = max(0,    cx // ts)
    start_row = max(0,    cy // ts)
    end_col   = min(cols, start_col + SCREEN_W // ts + 2)
    end_row   = min(rows, start_row + SCREEN_H // ts + 2)

    for row in range(start_row, end_row):
        for col in range(start_col, end_col):
            color = TILE_COLORS[grid[row][col]]
            rect  = pygame.Rect(
                col * ts - cx,
                row * ts - cy,
                ts,
                ts,
            )
            pygame.draw.rect(screen, color, rect)


def draw_map_ui(screen, font_ui, back_btn, seed, tile_size):
    draw_button(screen, back_btn, font_ui)

    hint = font_ui.render(
        f"Seed: {seed}   Zoom: {round(tile_size)}px   Arrows: move   Scroll/+/-: zoom   Esc: menu",
        True, (180, 180, 180),
    )
    screen.blit(hint, (10, SCREEN_H - 26))


# ---------------------------------------------------------------------------
# Camera utilities
# ---------------------------------------------------------------------------

def clamp_camera(cam_x, cam_y, tile_size):
    map_px_w = MAP_W * tile_size
    map_px_h = MAP_H * tile_size
    cam_x = max(0, min(cam_x, max(0, map_px_w - SCREEN_W)))
    cam_y = max(0, min(cam_y, max(0, map_px_h - SCREEN_H)))
    return cam_x, cam_y


def zoom_camera(cam_x, cam_y, old_size, new_size):
    """Keep the screen center fixed while zooming."""
    scale = new_size / old_size
    center_x = cam_x + SCREEN_W // 2
    center_y = cam_y + SCREEN_H // 2
    cam_x = int(center_x * scale) - SCREEN_W // 2
    cam_y = int(center_y * scale) - SCREEN_H // 2
    return clamp_camera(cam_x, cam_y, new_size)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
    pygame.display.set_caption("Sheep Island")
    clock = pygame.time.Clock()

    font_title = pygame.font.SysFont("Arial", 72, bold=True)
    font_ui    = pygame.font.SysFont("Arial", 20)

    # --- Title screen buttons ---
    bw, bh = 260, 58
    bx = SCREEN_W // 2 - bw // 2

    gen_btn = {
        "label": "Generate Map",
        "rect":  pygame.Rect(bx, SCREEN_H // 2 + 20, bw, bh),
        "color": (55, 130, 55),
    }
    quit_btn = {
        "label": "Quit",
        "rect":  pygame.Rect(bx, SCREEN_H // 2 + 100, bw, bh),
        "color": (150, 55, 55),
    }
    title_buttons = [gen_btn, quit_btn]

    back_btn = {
        "label": "Back to Menu",
        "rect":  pygame.Rect(SCREEN_W - 165, 10, 155, 40),
        "color": (70, 70, 110),
    }

    # --- Game state ---
    state        = STATE_TITLE
    grid         = None
    current_seed = random.randint(0, 999_999)
    tile_size    = TILE_SIZE_DEFAULT
    cam_x, cam_y = 0, 0

    # ---------------------------------------------------------------------------
    # Game loop
    # ---------------------------------------------------------------------------
    while True:
        clock.tick(FPS)
        mouse_pos = pygame.mouse.get_pos()

        # --- Events ---
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

            if event.type == pygame.KEYDOWN:
                if state == STATE_MAP:
                    if event.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                        new_size = min(TILE_SIZE_MAX, tile_size * ZOOM_FACTOR)
                        cam_x, cam_y = zoom_camera(cam_x, cam_y, tile_size, new_size)
                        tile_size = new_size

                    elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                        new_size = max(TILE_SIZE_MIN, tile_size / ZOOM_FACTOR)
                        cam_x, cam_y = zoom_camera(cam_x, cam_y, tile_size, new_size)
                        tile_size = new_size

                    elif event.key == pygame.K_ESCAPE:
                        state = STATE_TITLE

            if event.type == pygame.MOUSEWHEEL and state == STATE_MAP:
                if event.y > 0:
                    new_size = min(TILE_SIZE_MAX, tile_size * ZOOM_FACTOR)
                else:
                    new_size = max(TILE_SIZE_MIN, tile_size / ZOOM_FACTOR)
                cam_x, cam_y = zoom_camera(cam_x, cam_y, tile_size, new_size)
                tile_size = new_size

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if state == STATE_TITLE:
                    if gen_btn["rect"].collidepoint(mouse_pos):
                        current_seed = random.randint(0, 999_999)
                        generator    = MapGenerator(MAP_W, MAP_H, seed=current_seed)
                        grid         = generator.generate()
                        tile_size    = TILE_SIZE_DEFAULT
                        cam_x        = max(0.0, (MAP_W * tile_size - SCREEN_W) / 2)
                        cam_y        = max(0.0, (MAP_H * tile_size - SCREEN_H) / 2)
                        state        = STATE_MAP

                    elif quit_btn["rect"].collidepoint(mouse_pos):
                        pygame.quit()
                        sys.exit()

                elif state == STATE_MAP:
                    if back_btn["rect"].collidepoint(mouse_pos):
                        state = STATE_TITLE

        # --- Camera movement (held keys) ---
        if state == STATE_MAP:
            speed = max(1.0, CAMERA_SPEED * tile_size / TILE_SIZE_DEFAULT)
            keys = pygame.key.get_pressed()
            if keys[pygame.K_LEFT]:
                cam_x -= speed
            if keys[pygame.K_RIGHT]:
                cam_x += speed
            if keys[pygame.K_UP]:
                cam_y -= speed
            if keys[pygame.K_DOWN]:
                cam_y += speed
            cam_x, cam_y = clamp_camera(cam_x, cam_y, tile_size)

        # --- Render ---
        if state == STATE_TITLE:
            draw_title(screen, font_title, font_ui, title_buttons)

        elif state == STATE_MAP:
            screen.fill((0, 0, 0))
            draw_map(screen, grid, tile_size, cam_x, cam_y)
            draw_map_ui(screen, font_ui, back_btn, current_seed, tile_size)

        pygame.display.flip()


if __name__ == "__main__":
    main()
