import math
from selenium.webdriver.common.action_chains import ActionChains

class DragEngine:
    """Handles hybrid drag and drop operations (JS + Real Mouse emulation)."""
    def __init__(self, sb):
        self.sb = sb

    def _bezier_curve(self, start, end, num_points=10, control_variance=0.3):
        """Generates points along a quadratic bezier curve for human-like movement."""
        x0, y0 = start
        x2, y2 = end
        
        # Control point slightly offset
        dx = x2 - x0
        dy = y2 - y0
        
        # Add variance to y based on the distance
        cx = x0 + dx / 2
        cy = y0 + dy / 2 + (abs(dx) * control_variance)
        
        points = []
        for i in range(num_points + 1):
            t = i / num_points
            # Quadratic bezier formula: (1-t)^2 * P0 + 2(1-t)t * P1 + t^2 * P2
            x = int((1 - t)**2 * x0 + 2 * (1 - t) * t * cx + t**2 * x2)
            y = int((1 - t)**2 * y0 + 2 * (1 - t) * t * cy + t**2 * y2)
            points.append((x, y))
            
        return points

    def human_like_drag(self, selector: str, target_x: int) -> bool:
        """Simulate human mouse movement with a bezier curve using ActionChains."""
        try:
            element = self.sb.find_element(selector)
            
            # Since ActionChains move_by_offset is relative to the current mouse position,
            # we need to be careful. The first click_and_hold moves the mouse to the center of the element.
            actions = ActionChains(self.sb.driver)
            actions.click_and_hold(element)
            
            # Start position is 0 (relative to where we just clicked)
            # End position is target_x relative to the element
            points = self._bezier_curve((0, 0), (target_x, 0), num_points=5)
            
            prev_x, prev_y = 0, 0
            for px, py in points[1:]:
                # Move relative to the previous point
                actions.move_by_offset(px - prev_x, py - prev_y)
                prev_x, prev_y = px, py
                
            actions.release()
            actions.perform()
            return True
        except Exception as e:
            print(f"DEBUG: human_like_drag failed: {e}")
            return False

    def js_trigger(self, selector: str) -> bool:
        """Try to trigger checkin via JavaScript event."""
        try:
            self.sb.execute_script("""
                const el = document.querySelector(arguments[0]);
                if (el) {
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    // Trigger any custom framework events if jQuery is present
                    if (window.jQuery) {
                        try {
                            jQuery(el).trigger('change');
                        } catch(e) {}
                    }
                }
            """, selector)
            return True
        except Exception as e:
            print(f"DEBUG: js_trigger failed: {e}")
            return False

    def perform_drag(self, selector: str, drag_distance: int) -> bool:
        """Execute the hybrid drag logic."""
        # Step 1: Real Mouse Drag (headless safe under Xvfb)
        print(f"DEBUG: Attempting human-like ActionChains drag by {drag_distance}px...")
        if self.human_like_drag(selector, drag_distance):
            self.sb.sleep(2)
            return True
            
        # Step 2: Fallback to SeleniumBase's native drag
        print("DEBUG: human-like drag failed, falling back to sb.drag_and_drop_with_offset...")
        try:
            self.sb.drag_and_drop_with_offset(selector, drag_distance, 0)
            self.sb.sleep(2)
            return True
        except Exception as e:
            print(f"DEBUG: Fallback drag failed: {e}")
            
        # Step 3: Last resort JavaScript Trigger
        print("DEBUG: Falling back to js_trigger...")
        return self.js_trigger(selector)
